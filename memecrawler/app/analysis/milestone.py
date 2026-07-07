"""
Milestone Tracker.

Continues monitoring tokens that have been alerted (state = TRACKING)
and notifies when they hit significant price milestones or adverse events.

Milestones:
    +25%, +50%, +100%, +200%, +500%, +1000% from entry price
    All-Time High (ATH)
    -30% drawdown from ATH
    -50% drawdown from ATH
    Rug (price -80% in 1 scan cycle or vs ATH)
    Death (volume ≈ 0, liquidity ≈ 0 for multiple cycles)

Entry price is taken from the price at the time the alert was dispatched
(recorded in the outcomes table).

Sprint 3.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, TYPE_CHECKING

from app.analysis.models import MilestoneKind
from app.models.token import TokenData
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.database.manager import DatabaseManager
    from app.telegram.bot import TelegramBot

logger = logging.getLogger(__name__)

_RUG_THRESHOLD_PCT: float = -80.0   # -80% vs ATH = rug
_DEATH_VOLUME_USD: float = 100.0    # < $100 volume = potential death
_DEATH_LIQ_USD: float = 500.0       # < $500 liquidity = potential death

# Gain milestone thresholds (percentage above entry price)
_GAIN_MILESTONES: list[tuple[float, MilestoneKind]] = [
    (25.0, MilestoneKind.GAIN_25),
    (50.0, MilestoneKind.GAIN_50),
    (100.0, MilestoneKind.GAIN_100),
    (200.0, MilestoneKind.GAIN_200),
    (500.0, MilestoneKind.GAIN_500),
    (1000.0, MilestoneKind.GAIN_1000),
]


class MilestoneTracker:
    """
    Tracks post-alert token performance and dispatches milestone notifications.

    Parameters
    ----------
    db:
        Open DatabaseManager instance.
    telegram_bot:
        Live TelegramBot instance.  None = log-only mode.
    """

    def __init__(
        self,
        db: "DatabaseManager",
        telegram_bot: Optional["TelegramBot"] = None,
    ) -> None:
        self._db = db
        self._bot = telegram_bot
        self._milestones_fired: int = 0

    # ── Main check ────────────────────────────────────────────────────────

    async def check(
        self,
        mint: str,
        symbol: str,
        current: TokenData,
    ) -> None:
        """
        Check for new milestone achievements for a TRACKING token.

        Parameters
        ----------
        mint:
            Token mint address.
        symbol:
            Token ticker (cosmetic).
        current:
            Latest market data.
        """
        if current.price_usd is None:
            return

        outcome = await self._get_or_create_outcome(mint, current)
        if outcome is None:
            return

        entry_price = outcome.get("entry_price_usd")
        if entry_price is None or entry_price <= 0:
            return

        current_price = current.price_usd
        peak_price = outcome.get("peak_price_usd") or entry_price

        # Update peak
        if current_price > peak_price:
            peak_price = current_price
            await self._update_peak(mint, peak_price, current_price)

        # ── Gain milestones ───────────────────────────────────────────────
        gain_pct = (current_price - entry_price) / entry_price * 100
        for threshold, kind in _GAIN_MILESTONES:
            if gain_pct >= threshold:
                await self._fire_once(mint, symbol, kind, {
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "gain_pct": round(gain_pct, 2),
                })

        # ── ATH milestone ────────────────────────────────────────────────
        if current_price >= peak_price and gain_pct > 0:
            await self._fire_once(mint, symbol, MilestoneKind.ATH, {
                "ath_price": current_price,
                "gain_pct": round(gain_pct, 2),
            })

        # ── Drawdown milestones ───────────────────────────────────────────
        if peak_price > 0:
            drawdown_pct = (current_price - peak_price) / peak_price * 100
            if drawdown_pct <= -50:
                await self._fire_once(mint, symbol, MilestoneKind.DRAWDOWN_50, {
                    "peak_price": peak_price,
                    "current_price": current_price,
                    "drawdown_pct": round(drawdown_pct, 2),
                })
            elif drawdown_pct <= -30:
                await self._fire_once(mint, symbol, MilestoneKind.DRAWDOWN_30, {
                    "peak_price": peak_price,
                    "current_price": current_price,
                    "drawdown_pct": round(drawdown_pct, 2),
                })

            # ── Rug detection ─────────────────────────────────────────────
            if drawdown_pct <= _RUG_THRESHOLD_PCT:
                await self._fire_once(mint, symbol, MilestoneKind.RUG, {
                    "peak_price": peak_price,
                    "current_price": current_price,
                    "drawdown_pct": round(drawdown_pct, 2),
                })

        # ── Death detection ───────────────────────────────────────────────
        vol = current.volume_24h_usd or 0.0
        liq = current.liquidity_usd or 0.0
        if vol < _DEATH_VOLUME_USD and liq < _DEATH_LIQ_USD:
            await self._fire_once(mint, symbol, MilestoneKind.DEATH, {
                "volume_usd": vol,
                "liquidity_usd": liq,
            })

        # Update current price in outcomes
        await self._update_current(mint, current_price, gain_pct)

    # ── DB helpers ────────────────────────────────────────────────────────

    async def _get_or_create_outcome(
        self, mint: str, current: TokenData
    ) -> Optional[dict[str, Any]]:
        """Fetch or create the outcome record for a TRACKING token."""
        row = await self._db.fetchone(
            "SELECT * FROM outcomes WHERE mint = ?", (mint,)
        )
        if row:
            return dict(row)

        # Create new outcome using current price as entry
        entry_price = current.price_usd
        if entry_price is None:
            return None

        now = utcnow_iso()
        await self._db.execute(
            """
            INSERT INTO outcomes
                (mint, entry_price_usd, peak_price_usd, current_price_usd,
                 peak_gain_pct, current_gain_pct, outcome,
                 tracked_since, last_updated)
            VALUES (?, ?, ?, ?, 0, 0, 'TRACKING', ?, ?)
            """,
            (mint, entry_price, entry_price, entry_price, now, now),
        )
        return {
            "entry_price_usd": entry_price,
            "peak_price_usd": entry_price,
            "current_price_usd": entry_price,
        }

    async def _update_peak(self, mint: str, peak: float, current: float) -> None:
        entry = await self._entry_price(mint) or peak
        gain = (peak - entry) / max(entry, 1e-9) * 100
        await self._db.execute(
            """
            UPDATE outcomes
            SET peak_price_usd = ?, current_price_usd = ?,
                peak_gain_pct = ?, last_updated = ?
            WHERE mint = ?
            """,
            (peak, current, round(gain, 2), utcnow_iso(), mint),
        )

    async def _update_current(
        self, mint: str, price: float, gain_pct: float
    ) -> None:
        await self._db.execute(
            """
            UPDATE outcomes
            SET current_price_usd = ?, current_gain_pct = ?, last_updated = ?
            WHERE mint = ?
            """,
            (price, round(gain_pct, 2), utcnow_iso(), mint),
        )

    async def _entry_price(self, mint: str) -> Optional[float]:
        row = await self._db.fetchone(
            "SELECT entry_price_usd FROM outcomes WHERE mint = ?", (mint,)
        )
        if row and row["entry_price_usd"]:
            return float(row["entry_price_usd"])
        return None

    async def _fire_once(
        self,
        mint: str,
        symbol: str,
        kind: MilestoneKind,
        metadata: dict[str, Any],
    ) -> None:
        """Record and notify a milestone, but only once per token+kind."""
        existing = await self._db.fetchone(
            "SELECT id FROM milestones WHERE mint = ? AND kind = ?",
            (mint, kind.value),
        )
        if existing:
            return   # Already notified

        now = utcnow_iso()
        value = metadata.get("gain_pct") or metadata.get("drawdown_pct") or 0.0
        await self._db.execute(
            """
            INSERT INTO milestones (mint, kind, value, achieved_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mint, kind.value, value, now, json.dumps(metadata)),
        )
        self._milestones_fired += 1
        logger.info(
            "Milestone %s achieved for %s: %s",
            kind.value,
            mint[:12],
            metadata,
        )
        await self._notify(mint, symbol, kind, metadata)

    async def _notify(
        self,
        mint: str,
        symbol: str,
        kind: MilestoneKind,
        metadata: dict[str, Any],
    ) -> None:
        """Send Telegram notification for a milestone."""
        msg = _build_milestone_message(mint, symbol, kind, metadata)
        if self._bot is None or not self._bot.is_running:
            logger.info("Milestone notification (no bot): %s", msg[:80])
            return
        try:
            await self._bot.send_message(msg)
        except Exception as exc:
            logger.error("Failed to send milestone notification: %s", exc)

    # ── Diagnostics ───────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        return {"milestones_fired_session": self._milestones_fired}


# ── Message builders ──────────────────────────────────────────────────────────

def _build_milestone_message(
    mint: str,
    symbol: str,
    kind: MilestoneKind,
    metadata: dict[str, Any],
) -> str:
    ticker = symbol or mint[:8]
    label = kind.label

    details_lines: list[str] = []
    if "gain_pct" in metadata:
        details_lines.append(f"Gain: +{metadata['gain_pct']:.1f}%")
    if "current_price" in metadata:
        details_lines.append(f"Price: ${metadata['current_price']:.6f}")
    if "drawdown_pct" in metadata:
        details_lines.append(f"Drawdown: {metadata['drawdown_pct']:.1f}%")
    if "volume_usd" in metadata:
        details_lines.append(f"Volume: ${metadata.get('volume_usd', 0):.0f}")

    detail_str = " | ".join(details_lines) if details_lines else ""

    return (
        f"📍 <b>Milestone: {ticker} — {label}</b>\n"
        f"{detail_str}\n"
        f"<code>{mint}</code>"
    )
