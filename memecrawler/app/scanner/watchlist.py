"""
Watchlist Manager.

Manages the token watchlist:
- Adding new tokens (idempotent)
- Retrieving tokens due for scanning (priority-ordered)
- Recording scan results and history
- Deterministic state machine transitions
- Adaptive priority updates

Every watched token is persisted to SQLite. The in-process cache is used
to avoid redundant DB round-trips for hot paths (e.g. add-token checks).

Sprint 2.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.cache.manager import CacheManager
from app.database.manager import DatabaseManager
from app.models.token import (
    ScanPriority,
    TokenData,
    TokenState,
    WatchEntry,
    is_valid_transition,
)
from app.utils.errors import InvalidStateTransitionError
from app.utils.time_utils import utcnow, utcnow_iso

logger = logging.getLogger(__name__)


class WatchlistManager:
    """
    Owns the watchlist lifecycle: add, scan, transition, archive.

    Parameters
    ----------
    db:
        Open :class:`~app.database.manager.DatabaseManager` instance.
    cache:
        In-process :class:`~app.cache.manager.CacheManager` for hot-path
        deduplication (avoids repeated DB round-trips per discovery cycle).
    """

    def __init__(self, db: DatabaseManager, cache: CacheManager) -> None:
        self._db = db
        self._cache = cache
        self._tokens_added: int = 0
        self._scans_recorded: int = 0
        self._transitions: int = 0

    # ── Add token ──────────────────────────────────────────────────────────

    async def add_token(
        self,
        token: TokenData,
        *,
        reason: str = "auto_discovery",
    ) -> bool:
        """
        Add a token to the watchlist.

        This method is idempotent: if the token is already on the watchlist
        (checked via cache then DB), it returns False without writing.

        Parameters
        ----------
        token:
            The discovered token to add.
        reason:
            Human-readable description of why this token was added.

        Returns
        -------
        bool
            True when the token was newly added; False when already present.
        """
        cache_key = f"watchlist:exists:{token.mint}"
        if self._cache.has(cache_key):
            return False

        existing = await self._db.fetchone(
            "SELECT mint FROM watchlist WHERE mint = ?", (token.mint,)
        )
        if existing:
            self._cache.set(cache_key, True, ttl=3600.0)
            return False

        now = utcnow_iso()
        watch_id = str(uuid.uuid4())

        await self._db.execute(
            """
            INSERT INTO watchlist (
                mint, symbol, name, watch_id,
                added_at, first_seen_at, last_seen_at,
                reason, state, priority, scan_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                token.mint,
                token.symbol or "",
                token.name or "",
                watch_id,
                now,
                now,
                now,
                reason,
                TokenState.DISCOVERED.value,
                ScanPriority.LOW.value,
            ),
        )
        self._cache.set(cache_key, True, ttl=3600.0)
        self._tokens_added += 1
        logger.info(
            "Watchlist ADD: %s (%s) reason=%s watch_id=%s",
            token.symbol or "?",
            token.mint[:12],
            reason,
            watch_id,
        )
        return True

    # ── Retrieve due tokens ────────────────────────────────────────────────

    async def get_due_tokens(self) -> list[WatchEntry]:
        """
        Return all watchlist tokens that are due for a scan.

        Tokens are returned in priority order (CRITICAL first, then HIGH,
        MEDIUM, LOW). Within the same priority, tokens that have never been
        scanned come first, then oldest ``last_scan_at`` first.

        Tokens in the ARCHIVED or TRACKING state are excluded.

        Returns
        -------
        list[WatchEntry]
            Ordered list of entries ready to be scanned.
        """
        now = utcnow_iso()
        rows = await self._db.fetchall(
            """
            SELECT *
            FROM   watchlist
            WHERE  state NOT IN (?, ?)
              AND  (next_scan_at IS NULL OR next_scan_at <= ?)
            ORDER BY
                CASE priority
                    WHEN 'CRITICAL' THEN 0
                    WHEN 'HIGH'     THEN 1
                    WHEN 'MEDIUM'   THEN 2
                    WHEN 'LOW'      THEN 3
                    ELSE 4
                END ASC,
                last_scan_at ASC NULLS FIRST
            """,
            (TokenState.ARCHIVED.value, TokenState.TRACKING.value, now),
        )
        return [_row_to_entry(row) for row in rows]

    # ── Record scan ────────────────────────────────────────────────────────

    async def record_scan(
        self,
        mint: str,
        token_data: Optional[TokenData],
    ) -> None:
        """
        Persist the result of a completed token scan.

        Updates the watchlist row (scan count, timestamps, latest market
        snapshot) and inserts a history record. When ``token_data`` is
        None (e.g. the provider failed), only the scan count and timestamp
        are updated.

        Parameters
        ----------
        mint:
            Token mint address.
        token_data:
            Market data from the scan. May be None on provider failure.
        """
        now = utcnow_iso()

        if token_data is None:
            # Provider returned nothing — advance the counter AND schedule the
            # next scan so the token doesn't remain perpetually due (which would
            # waste API calls by retrying on every cycle).
            priority_row = await self._db.fetchone(
                "SELECT priority FROM watchlist WHERE mint = ?", (mint,)
            )
            if priority_row:
                try:
                    failed_priority = ScanPriority(priority_row["priority"])
                except ValueError:
                    failed_priority = ScanPriority.LOW
            else:
                failed_priority = ScanPriority.LOW

            next_scan = _next_scan_iso(failed_priority)
            await self._db.execute(
                """
                UPDATE watchlist
                SET scan_count   = scan_count + 1,
                    last_scan_at = ?,
                    next_scan_at = ?
                WHERE mint = ?
                """,
                (now, next_scan, mint),
            )
            self._scans_recorded += 1
            return

        # Determine next scan time from current priority
        priority_row = await self._db.fetchone(
            "SELECT priority FROM watchlist WHERE mint = ?", (mint,)
        )
        if priority_row:
            try:
                priority = ScanPriority(priority_row["priority"])
            except ValueError:
                priority = ScanPriority.LOW
        else:
            priority = ScanPriority.LOW

        next_scan = _next_scan_iso(priority)

        await self._db.execute(
            """
            UPDATE watchlist
            SET scan_count     = scan_count + 1,
                last_scan_at   = ?,
                last_seen_at   = ?,
                next_scan_at   = ?,
                price_usd      = ?,
                market_cap_usd = ?,
                liquidity_usd  = ?,
                volume_24h_usd = ?
            WHERE mint = ?
            """,
            (
                now,
                now,
                next_scan,
                token_data.price_usd,
                token_data.market_cap_usd,
                token_data.liquidity_usd,
                token_data.volume_24h_usd,
                mint,
            ),
        )

        # Insert history record
        await self._db.execute(
            """
            INSERT INTO history (
                mint, price_usd, market_cap_usd, volume_24h_usd,
                liquidity_usd, buys_5m, sells_5m, age_seconds,
                recorded_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mint,
                token_data.price_usd,
                token_data.market_cap_usd,
                token_data.volume_24h_usd,
                token_data.liquidity_usd,
                token_data.buys_5m,
                token_data.sells_5m,
                token_data.age_seconds,
                now,
                token_data.provider,
            ),
        )
        self._scans_recorded += 1
        logger.debug(
            "Scan recorded: %s  MC=$%s  liq=$%s  vol=$%s",
            mint[:12],
            _fmt(token_data.market_cap_usd),
            _fmt(token_data.liquidity_usd),
            _fmt(token_data.volume_24h_usd),
        )

    # ── State machine ──────────────────────────────────────────────────────

    async def transition_state(
        self,
        mint: str,
        new_state: TokenState,
    ) -> bool:
        """
        Transition a token to a new state (deterministic validation).

        Parameters
        ----------
        mint:
            Token mint address.
        new_state:
            The target state.

        Returns
        -------
        bool
            True on success; False when the token is not found.

        Raises
        ------
        InvalidStateTransitionError
            When the transition from the current state to ``new_state``
            is not permitted by the state machine.
        """
        row = await self._db.fetchone(
            "SELECT state FROM watchlist WHERE mint = ?", (mint,)
        )
        if not row:
            logger.warning(
                "transition_state called for unknown mint %s", mint[:12]
            )
            return False

        try:
            current = TokenState(row["state"])
        except ValueError:
            current = TokenState.DISCOVERED

        if not is_valid_transition(current, new_state):
            raise InvalidStateTransitionError(
                f"Invalid transition for {mint[:12]}: "
                f"{current.value} → {new_state.value}",
                code="INVALID_TRANSITION",
            )

        await self._db.execute(
            "UPDATE watchlist SET state = ? WHERE mint = ?",
            (new_state.value, mint),
        )
        self._transitions += 1
        logger.info(
            "State transition: %s  %s → %s",
            mint[:12],
            current.value,
            new_state.value,
        )
        return True

    # ── Priority ───────────────────────────────────────────────────────────

    async def update_priority(
        self,
        mint: str,
        priority: ScanPriority,
    ) -> bool:
        """
        Update the scan priority for a watchlist token.

        No-ops silently when the priority is unchanged.

        Parameters
        ----------
        mint:
            Token mint address.
        priority:
            The new priority level.

        Returns
        -------
        bool
            True when the priority was changed; False when unchanged.
        """
        row = await self._db.fetchone(
            "SELECT priority FROM watchlist WHERE mint = ?", (mint,)
        )
        if not row:
            return False
        if row["priority"] == priority.value:
            return False

        old_priority = row["priority"]
        await self._db.execute(
            "UPDATE watchlist SET priority = ? WHERE mint = ?",
            (priority.value, mint),
        )
        logger.info(
            "Priority change: %s  %s → %s",
            mint[:12],
            old_priority,
            priority.value,
        )
        return True

    # ── Read helpers ───────────────────────────────────────────────────────

    async def get_all(self, *, include_archived: bool = False) -> list[WatchEntry]:
        """Return all watchlist entries, optionally including archived ones."""
        if include_archived:
            rows = await self._db.fetchall(
                "SELECT * FROM watchlist ORDER BY added_at DESC"
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM watchlist WHERE state != ? ORDER BY added_at DESC",
                (TokenState.ARCHIVED.value,),
            )
        return [_row_to_entry(row) for row in rows]

    async def get_by_state(self, state: TokenState) -> list[WatchEntry]:
        """Return all tokens in a specific state."""
        rows = await self._db.fetchall(
            "SELECT * FROM watchlist WHERE state = ? ORDER BY added_at DESC",
            (state.value,),
        )
        return [_row_to_entry(row) for row in rows]

    async def count_by_state(self) -> dict[str, int]:
        """Return a count of tokens grouped by state."""
        rows = await self._db.fetchall(
            "SELECT state, COUNT(*) AS cnt FROM watchlist GROUP BY state"
        )
        return {row["state"]: row["cnt"] for row in rows}

    async def count_active(self) -> int:
        """Return the number of non-archived, non-tracking tokens."""
        row = await self._db.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM watchlist
            WHERE state NOT IN (?, ?)
            """,
            (TokenState.ARCHIVED.value, TokenState.TRACKING.value),
        )
        return row["cnt"] if row else 0

    async def get_tracking_tokens(self) -> list[WatchEntry]:
        """
        Return TRACKING tokens that are due for a milestone check.

        Tokens enter TRACKING after an alert is dispatched.  They are
        excluded from :meth:`get_due_tokens` but need periodic milestone
        scans.  Uses the same ``next_scan_at`` scheduling mechanism;
        TRACKING tokens default to MEDIUM scan cadence (120 s).

        Returns
        -------
        list[WatchEntry]
            Tracking tokens whose next_scan_at is in the past (or NULL).
        """
        now = utcnow_iso()
        rows = await self._db.fetchall(
            """
            SELECT *
            FROM   watchlist
            WHERE  state = ?
              AND  (next_scan_at IS NULL OR next_scan_at <= ?)
            ORDER BY last_scan_at ASC NULLS FIRST
            """,
            (TokenState.TRACKING.value, now),
        )
        return [_row_to_entry(row) for row in rows]

    async def get_recent_history(
        self, mint: str, limit: int = 10
    ) -> list[dict[str, object]]:
        """Return the most recent scan history records for a token."""
        rows = await self._db.fetchall(
            """
            SELECT price_usd, market_cap_usd, volume_24h_usd, liquidity_usd,
                   buys_5m, sells_5m, age_seconds, recorded_at, source
            FROM   history
            WHERE  mint = ?
            ORDER BY recorded_at DESC
            LIMIT  ?
            """,
            (mint, limit),
        )
        return [dict(row) for row in rows]

    # ── Diagnostics ────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """Return runtime counters for the /diagnostics endpoint."""
        return {
            "tokens_added": self._tokens_added,
            "scans_recorded": self._scans_recorded,
            "state_transitions": self._transitions,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_entry(row: object) -> WatchEntry:
    """Convert an ``aiosqlite.Row`` to a :class:`WatchEntry`."""
    r = dict(row)  # type: ignore[call-overload]

    def _state(v: object) -> TokenState:
        try:
            return TokenState(v)
        except ValueError:
            return TokenState.DISCOVERED

    def _priority(v: object) -> ScanPriority:
        try:
            return ScanPriority(v)
        except ValueError:
            return ScanPriority.LOW

    now_iso = utcnow_iso()
    return WatchEntry(
        watch_id=r.get("watch_id") or "",
        mint=r["mint"],
        symbol=r.get("symbol") or "",
        name=r.get("name") or "",
        state=_state(r.get("state", "DISCOVERED")),
        priority=_priority(r.get("priority", "LOW")),
        reason_added=r.get("reason") or "",
        first_seen_at=r.get("first_seen_at") or r.get("added_at") or now_iso,
        last_seen_at=r.get("last_seen_at") or r.get("added_at") or now_iso,
        scan_count=int(r.get("scan_count") or 0),
        last_scan_at=r.get("last_scan_at"),
        next_scan_at=r.get("next_scan_at"),
        price_usd=_float(r.get("price_usd")),
        market_cap_usd=_float(r.get("market_cap_usd")),
        liquidity_usd=_float(r.get("liquidity_usd")),
        volume_24h_usd=_float(r.get("volume_24h_usd")),
        # Sprint 3 scoring columns
        score=_float(r.get("score")),
        confidence=_float(r.get("confidence")),
        risk_level=str(r.get("risk_level") or "UNKNOWN"),
        alert_sent_at=r.get("alert_sent_at"),
    )


def _next_scan_iso(priority: ScanPriority) -> str:
    """Return an ISO-8601 timestamp for the next scheduled scan."""
    delta = timedelta(seconds=priority.interval_seconds)
    return (datetime.now(tz=timezone.utc) + delta).isoformat()


def _float(v: object) -> Optional[float]:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "?"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:.0f}"
