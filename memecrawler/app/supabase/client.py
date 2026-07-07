"""
Supabase client — real synchronisation implementation.

Syncs local SQLite data (watchlist, alerts, tokens) to the Supabase
cloud database using incremental watermarks so only changed rows are
pushed on each cycle.

Requires: SUPABASE_URL and SUPABASE_KEY in settings.py (already set).
The remote Supabase schema must have the three tables defined below.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# ── Remote table names ────────────────────────────────────────────────────────
# These must match the table names you have created (or will create) in your
# Supabase project.  Each mirrors the local SQLite table structure.

_TABLE_TOKENS    = "tokens"
_TABLE_WATCHLIST = "watchlist"
_TABLE_ALERTS    = "alerts"

# Maximum rows sent per batch to avoid exceeding Supabase request limits.
_BATCH_SIZE = 100


class SupabaseClient:
    """
    Client for Supabase cloud database synchronisation.

    Parameters
    ----------
    url:
        Supabase project URL (e.g. ``https://xyzcompany.supabase.co``).
    key:
        Supabase anon or service role key.
    db:
        Open DatabaseManager instance used to read local data.
        Required for sync operations; set via :meth:`set_db`.
    """

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key
        self._db: Optional["DatabaseManager"] = None
        self._client: Any = None
        self._connected: bool = False
        self._connect_error: Optional[str] = None
        self._syncs_attempted: int = 0
        self._syncs_succeeded: int = 0
        # Per-table watermarks for incremental sync — ISO 8601 timestamps.
        self._last_synced: dict[str, Optional[str]] = {
            _TABLE_TOKENS:    None,
            _TABLE_ALERTS:    None,
            _TABLE_WATCHLIST: None,
        }

    def set_db(self, db: "DatabaseManager") -> None:
        """Inject the database manager (called from main.py after construction)."""
        self._db = db

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Create the Supabase async client and verify connectivity with a
        lightweight read against the watchlist table.
        """
        try:
            from supabase import create_async_client
            self._client = await create_async_client(self._url, self._key)
            # Minimal ping: fetch 1 row from watchlist (safe even if table is empty)
            await self._client.table(_TABLE_WATCHLIST).select("mint").limit(1).execute()
            self._connected = True
            logger.info("Supabase: connected to %s…", self._url[:40])
        except Exception as exc:
            self._connected = False
            self._connect_error = str(exc)
            logger.warning("Supabase: connection failed — %s", exc)

    async def disconnect(self) -> None:
        """Close the Supabase connection gracefully."""
        if self._connected:
            try:
                if hasattr(self._client, "auth") and hasattr(self._client.auth, "close"):
                    await self._client.auth.close()
            except Exception:
                pass
            self._connected = False
            logger.info("Supabase: disconnected.")
        else:
            logger.debug("Supabase: disconnect() called — was not connected.")

    # ── Sync operations ────────────────────────────────────────────────────

    async def sync_tokens(self) -> None:
        """
        Upsert all tokens modified since the last sync watermark.

        Reads from the local ``tokens`` SQLite table and batch-upserts to
        the remote Supabase ``tokens`` table.  The ``updated_at`` column is
        used as the incremental watermark.
        """
        if not self._connected or self._client is None or self._db is None:
            return

        self._syncs_attempted += 1
        watermark = self._last_synced[_TABLE_TOKENS]
        try:
            if watermark:
                rows = await self._db.fetchall(
                    "SELECT * FROM tokens WHERE updated_at > ? ORDER BY updated_at LIMIT ?;",
                    (watermark, _BATCH_SIZE),
                )
            else:
                rows = await self._db.fetchall(
                    "SELECT * FROM tokens ORDER BY updated_at LIMIT ?;",
                    (_BATCH_SIZE,),
                )
            if not rows:
                return

            batch = [_row_to_dict(r) for r in rows]
            await self._client.table(_TABLE_TOKENS).upsert(
                batch, on_conflict="mint"
            ).execute()

            new_watermark = batch[-1].get("updated_at")
            if new_watermark:
                self._last_synced[_TABLE_TOKENS] = new_watermark
            self._syncs_succeeded += 1
            logger.debug("Supabase: synced %d token(s).", len(batch))
        except Exception as exc:
            logger.warning("Supabase sync_tokens failed: %s", exc)

    async def sync_alerts(self) -> None:
        """
        Replicate new alerts to Supabase since the last sync watermark.

        Uses ``sent_at`` as the watermark column.
        """
        if not self._connected or self._client is None or self._db is None:
            return

        self._syncs_attempted += 1
        watermark = self._last_synced[_TABLE_ALERTS]
        try:
            if watermark:
                rows = await self._db.fetchall(
                    "SELECT * FROM alerts WHERE sent_at > ? ORDER BY sent_at LIMIT ?;",
                    (watermark, _BATCH_SIZE),
                )
            else:
                rows = await self._db.fetchall(
                    "SELECT * FROM alerts ORDER BY sent_at LIMIT ?;",
                    (_BATCH_SIZE,),
                )
            if not rows:
                return

            batch = [_row_to_dict(r) for r in rows]
            await self._client.table(_TABLE_ALERTS).upsert(
                batch, on_conflict="id"
            ).execute()

            new_watermark = batch[-1].get("sent_at")
            if new_watermark:
                self._last_synced[_TABLE_ALERTS] = new_watermark
            self._syncs_succeeded += 1
            logger.debug("Supabase: synced %d alert(s).", len(batch))
        except Exception as exc:
            logger.warning("Supabase sync_alerts failed: %s", exc)

    async def sync_watchlist(self) -> None:
        """
        Replicate the watchlist table to Supabase.

        Uses ``last_seen_at`` as the watermark column.
        """
        if not self._connected or self._client is None or self._db is None:
            return

        self._syncs_attempted += 1
        watermark = self._last_synced[_TABLE_WATCHLIST]
        try:
            if watermark:
                rows = await self._db.fetchall(
                    "SELECT * FROM watchlist WHERE last_seen_at > ? ORDER BY last_seen_at LIMIT ?;",
                    (watermark, _BATCH_SIZE),
                )
            else:
                rows = await self._db.fetchall(
                    "SELECT * FROM watchlist ORDER BY last_seen_at LIMIT ?;",
                    (_BATCH_SIZE,),
                )
            if not rows:
                return

            batch = [_row_to_dict(r) for r in rows]
            await self._client.table(_TABLE_WATCHLIST).upsert(
                batch, on_conflict="mint"
            ).execute()

            new_watermark = batch[-1].get("last_seen_at")
            if new_watermark:
                self._last_synced[_TABLE_WATCHLIST] = new_watermark
            self._syncs_succeeded += 1
            logger.debug("Supabase: synced %d watchlist row(s).", len(batch))
        except Exception as exc:
            logger.warning("Supabase sync_watchlist failed: %s", exc)

    async def sync_all(self) -> None:
        """
        Run all three sync operations in sequence.

        Safe to call even when not connected — each sub-call short-circuits.
        """
        if not self._connected:
            return
        await self.sync_tokens()
        await self.sync_alerts()
        await self.sync_watchlist()

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True when the client has an active Supabase connection."""
        return self._connected

    # ── Status ────────────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """
        Return a status summary for the /health API endpoint.

        The Supabase key is never included in the output.
        """
        redacted_url = (
            self._url[:30] + "..." if len(self._url) > 30 else self._url
        ) if self._url else ""
        result: dict[str, Any] = {
            "connected": self._connected,
            "url": redacted_url,
            "syncs_attempted": self._syncs_attempted,
            "syncs_succeeded": self._syncs_succeeded,
            "last_synced": self._last_synced,
        }
        if self._connect_error and not self._connected:
            result["connect_error"] = self._connect_error
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert an aiosqlite.Row (or dict) to a plain dict safe for JSON."""
    try:
        return dict(row)
    except Exception:
        return {}
