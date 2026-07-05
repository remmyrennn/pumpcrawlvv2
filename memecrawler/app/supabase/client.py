"""
Supabase client — Sprint 4 interface.

This module defines the full planned API surface for cloud database
synchronisation. The synchronisation logic itself is intentionally deferred
(all methods are no-ops) until Supabase credentials are confirmed and
schema design is finalised.

Sprint 4 delivers:
- Complete interface with watermark tracking (last_synced_at per table).
- Connection lifecycle (connect / disconnect) wired into the startup sequence.
- Status reported in /health and via the /providers Telegram command.
- Ready for real implementation: replace each no-op with supabase-py calls.

When to implement the real sync:
- Supabase project created, service key available in SUPABASE_KEY env var.
- Remote schema matches local SQLite tables (tokens, watchlist, alerts).
- ENABLE_SUPABASE_SYNC=true in environment.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)


class SupabaseClient:
    """
    Client for Supabase cloud database synchronisation.

    Parameters
    ----------
    url:
        Supabase project URL (e.g. ``https://xyzcompany.supabase.co``).
    key:
        Supabase service role key (never the anon key in backend services).

    Notes
    -----
    All sync methods are no-ops until credentials are confirmed and
    ``connect()`` successfully establishes a connection. Callers must check
    ``is_connected`` before calling sync methods if they need guarantees.
    """

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        # Key is stored but never logged or exposed in info().
        self._key = key
        self._connected: bool = False
        self._connect_error: Optional[str] = None
        self._syncs_attempted: int = 0
        self._syncs_succeeded: int = 0
        # Per-table watermarks for incremental sync.
        self._last_synced: dict[str, Optional[str]] = {
            "tokens": None,
            "alerts": None,
            "watchlist": None,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Establish a connection to Supabase.

        Sprint 4: no-op interface — real implementation replaces this with
        supabase-py client initialisation and a ping to verify credentials.

        When implemented, this should:
        1. Import supabase-py and create a ``AsyncClient``.
        2. Call a lightweight health query to confirm connectivity.
        3. Set ``self._connected = True`` on success.
        """
        logger.info(
            "SupabaseClient: connect() called — interface ready, sync deferred. "
            "Set ENABLE_SUPABASE_SYNC=true and provide SUPABASE_URL + SUPABASE_KEY "
            "to activate real synchronisation."
        )
        # Do NOT set _connected = True here — the interface is not yet live.
        self._connected = False

    async def disconnect(self) -> None:
        """Close the Supabase connection gracefully."""
        if self._connected:
            logger.info("SupabaseClient: disconnecting.")
            self._connected = False
        else:
            logger.debug("SupabaseClient: disconnect() called — was not connected.")

    # ── Sync operations (no-op stubs) ────────────────────────────────────

    async def sync_tokens(self) -> None:
        """
        Upsert all token rows modified since ``last_synced["tokens"]``.

        Sprint 4 stub — no-op. Real implementation:
        1. Query local SQLite for tokens updated after the watermark.
        2. Batch-upsert to Supabase ``tokens`` table.
        3. Update ``_last_synced["tokens"]`` on success.
        """
        self._syncs_attempted += 1
        logger.debug("SupabaseClient: sync_tokens() — no-op (sync not yet live).")

    async def sync_alerts(self) -> None:
        """
        Replicate alert history to Supabase since the last sync watermark.

        Sprint 4 stub — no-op.
        """
        self._syncs_attempted += 1
        logger.debug("SupabaseClient: sync_alerts() — no-op (sync not yet live).")

    async def sync_watchlist(self) -> None:
        """
        Replicate the watchlist table to Supabase.

        Sprint 4 stub — no-op.
        """
        self._syncs_attempted += 1
        logger.debug("SupabaseClient: sync_watchlist() — no-op (sync not yet live).")

    async def sync_all(self) -> None:
        """
        Run all three sync operations in sequence.

        Safe to call even when not connected — each sub-call is a no-op.
        """
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

        Returns
        -------
        dict
            Keys: ``connected``, ``url`` (redacted), ``syncs_attempted``,
            ``syncs_succeeded``, ``last_synced``.
        """
        redacted_url = (
            self._url[:30] + "..." if len(self._url) > 30 else self._url
        ) if self._url else ""
        return {
            "connected": self._connected,
            "url": redacted_url,
            "syncs_attempted": self._syncs_attempted,
            "syncs_succeeded": self._syncs_succeeded,
            "last_synced": self._last_synced,
        }
