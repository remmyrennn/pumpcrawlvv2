"""
Alert Engine.

Dispatches Telegram alerts for tokens that have earned conviction across
multiple scans with a high enough score, confidence, and acceptable risk.

Design rules:
- Alert ONLY when:  multiple rescans + required score + required confidence
                    + acceptable risk.
- Duplicate prevention: SELECT-before-INSERT within a single cursor context
  (aiosqlite is single-connection; operations are serialised).
- No cooldown: once a token is alerted, it is never re-alerted (state = TRACKING).
- Race-safe: the cursor context manager wraps the check+insert atomically.

Sprint 3.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, TYPE_CHECKING

from app.analysis.models import EvaluationResult
from app.models.token import TokenState
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.database.manager import DatabaseManager
    from app.scanner.watchlist import WatchlistManager
    from app.telegram.bot import TelegramBot

logger = logging.getLogger(__name__)

_ALERT_TYPE = "conviction_alert"


class AlertEngine:
    """
    Checks alert eligibility and dispatches Telegram messages.

    Parameters
    ----------
    db:
        Open DatabaseManager instance.
    watchlist:
        WatchlistManager for state transitions after alert.
    telegram_bot:
        Live TelegramBot instance.  None = alert logging only.
    """

    def __init__(
        self,
        db: "DatabaseManager",
        watchlist: "WatchlistManager",
        telegram_bot: Optional["TelegramBot"] = None,
    ) -> None:
        self._db = db
        self._watchlist = watchlist
        self._bot = telegram_bot
        self._alerts_sent: int = 0

    # ── Public API ────────────────────────────────────────────────────────

    async def maybe_alert(
        self,
        evaluation: EvaluationResult,
        symbol: str = "",
        name: str = "",
        current_price_usd: Optional[float] = None,
    ) -> bool:
        """
        Dispatch an alert for the evaluated token if eligible and not yet alerted.

        Parameters
        ----------
        evaluation:
            The fully computed EvaluationResult.
        symbol:
            Token ticker for the message (optional, cosmetic).
        name:
            Token name for the message (optional, cosmetic).

        Returns
        -------
        bool
            True when an alert was dispatched; False when skipped (duplicate
            or ineligible).
        """
        if not evaluation.eligible_for_alert:
            return False

        # Check + insert in one cursor scope (atomic on single-connection SQLite).
        # _try_insert_alert now returns the built message on success (or None on
        # duplicate) so we avoid calling _build_alert_message twice.
        message = await self._try_insert_alert(
            evaluation, symbol, name, current_price_usd
        )
        if message is None:
            logger.debug(
                "Alert skipped for %s — duplicate detected.", evaluation.mint[:12]
            )
            return False

        # Transition state READY_FOR_ALERT → TRACKING (or HIGH_PRIORITY → TRACKING)
        try:
            current_state = await self._get_state(evaluation.mint)
            if current_state in (TokenState.HIGH_PRIORITY, TokenState.READY_FOR_ALERT):
                # Try direct HIGH_PRIORITY → TRACKING via two hops if needed
                if current_state == TokenState.HIGH_PRIORITY:
                    try:
                        await self._watchlist.transition_state(
                            evaluation.mint, TokenState.READY_FOR_ALERT
                        )
                    except Exception:
                        pass
                await self._watchlist.transition_state(
                    evaluation.mint, TokenState.TRACKING
                )
        except Exception as exc:
            logger.warning(
                "State transition failed after alert for %s: %s",
                evaluation.mint[:12],
                exc,
            )

        # Send the already-built Telegram message
        await self._send(message)
        self._alerts_sent += 1

        logger.info(
            "ALERT dispatched: %s  score=%.1f  conf=%.1f%%  risk=%s",
            evaluation.mint[:12],
            evaluation.final_score,
            evaluation.confidence,
            evaluation.risk_level.value,
        )
        return True

    # ── Internal ──────────────────────────────────────────────────────────

    async def _try_insert_alert(
        self,
        ev: EvaluationResult,
        symbol: str,
        name: str,
        current_price_usd: Optional[float] = None,
    ) -> Optional[str]:
        """
        Atomically check for duplicates and insert.

        Also seeds the outcomes row with the alert-time entry price so that
        MilestoneTracker can compute accurate gain percentages from day one.

        Returns the built alert message string on success, or None when this
        alert is a duplicate (already exists in the DB).  Building the message
        once here avoids a second redundant call in ``maybe_alert``.
        """
        now = utcnow_iso()
        metadata = json.dumps({
            "score": round(ev.final_score, 2),
            "confidence": round(ev.confidence, 2),
            "risk_level": ev.risk_level.value,
            "market_mode": ev.market_mode.value,
            "scan_count": ev.scan_count,
        })

        async with self._db.cursor() as cur:
            # Duplicate check
            await cur.execute(
                "SELECT id FROM alerts WHERE mint = ? AND alert_type = ?",
                (ev.mint, _ALERT_TYPE),
            )
            existing = await cur.fetchone()
            if existing:
                return None

            # Build the message once and reuse it for both the DB record and
            # the Telegram send — previously it was built a second time in
            # maybe_alert which wasted CPU and risked divergence.
            msg = _build_alert_message(ev, symbol, name)
            await cur.execute(
                """
                INSERT INTO alerts (mint, alert_type, message, sent_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ev.mint, _ALERT_TYPE, msg, now, metadata),
            )
            alert_id = cur.lastrowid  # type: ignore[attr-defined]

            # Seed outcomes with the alert-time entry price so that
            # MilestoneTracker.check() gets an accurate baseline.  Only create
            # when no existing outcome row is present (UNIQUE on mint).
            if current_price_usd is not None:
                await cur.execute(
                    "SELECT id FROM outcomes WHERE mint = ?", (ev.mint,)
                )
                existing_outcome = await cur.fetchone()
                if not existing_outcome:
                    await cur.execute(
                        """
                        INSERT INTO outcomes
                            (mint, alert_id, entry_price_usd, peak_price_usd,
                             current_price_usd, peak_gain_pct, current_gain_pct,
                             outcome, tracked_since, last_updated)
                        VALUES (?, ?, ?, ?, ?, 0, 0, 'TRACKING', ?, ?)
                        """,
                        (
                            ev.mint, alert_id,
                            current_price_usd, current_price_usd, current_price_usd,
                            now, now,
                        ),
                    )

            # Mark alert_sent_at on watchlist for /watchlist display
            await cur.execute(
                "UPDATE watchlist SET alert_sent_at = ? WHERE mint = ?",
                (now, ev.mint),
            )

        return msg

    async def _get_state(self, mint: str) -> Optional[TokenState]:
        row = await self._db.fetchone(
            "SELECT state FROM watchlist WHERE mint = ?", (mint,)
        )
        if not row:
            return None
        try:
            return TokenState(row["state"])
        except ValueError:
            return None

    async def _send(self, message: str) -> None:
        """Send message via Telegram.  Logs if bot is unavailable."""
        if self._bot is None or not self._bot.is_running:
            logger.info("Alert (no bot): %s", message[:120])
            return
        try:
            await self._bot.send_message(message)
        except Exception as exc:
            logger.error("Failed to send alert via Telegram: %s", exc)

    # ── Diagnostics ───────────────────────────────────────────────────────

    async def count_today(self) -> int:
        """Return alerts sent since midnight UTC today."""
        row = await self._db.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM alerts
            WHERE alert_type = ?
              AND date(sent_at) = date('now')
            """,
            (_ALERT_TYPE,),
        )
        return row["cnt"] if row else 0

    def info(self) -> dict[str, Any]:
        return {"alerts_sent_session": self._alerts_sent}


# ── Message builder ───────────────────────────────────────────────────────────

def _build_alert_message(ev: EvaluationResult, symbol: str, name: str) -> str:
    """Build the Telegram HTML alert message."""
    ticker = symbol or ev.mint[:8]
    token_name = name or "Unknown Token"

    score_bar = _score_bar(ev.final_score)
    risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "💀", "UNKNOWN": "⚪"}
    mode_icon = {"BULL": "🐂", "NEUTRAL": "➡️", "WEAK": "🐻"}

    top_reasons = ev.reasons[:4]
    reason_lines = "\n".join(f"  • {r}" for r in top_reasons)

    return (
        f"🚨 <b>MemeCrawler Alert — {ticker}</b>\n"
        f"<i>{token_name}</i>\n\n"
        f"<b>Score:</b>      {ev.final_score:.1f}/100 {score_bar}\n"
        f"<b>Confidence:</b> {ev.confidence:.0f}%\n"
        f"<b>Risk:</b>       {risk_icon.get(ev.risk_level.value, '⚪')} {ev.risk_level.value}\n"
        f"<b>Market:</b>     {mode_icon.get(ev.market_mode.value, '➡️')} {ev.market_mode.value}\n"
        f"<b>Scans:</b>      {ev.scan_count}\n\n"
        f"<b>Signals:</b>\n{reason_lines}\n\n"
        f"<code>{ev.mint}</code>\n"
        f"<i>Multiple confirmations required — DYOR. Not financial advice.</i>"
    )


def _score_bar(score: float) -> str:
    """Return a visual bar for the score."""
    filled = int(score / 10)
    return "█" * filled + "░" * (10 - filled)
