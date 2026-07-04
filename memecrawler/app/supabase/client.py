"""
Supabase client — prepared for Sprint 4.

This module defines the interface that Sprint 4 will implement for
cloud database synchronisation. The class is intentionally minimal in
Sprint 1 — it documents the planned API surface without executing any
network calls.

Sprint 4 implementation checklist
----------------------------------
- Authenticate with Supabase using the service key.
- Implement :meth:`sync_tokens` to upsert token records.
- Implement :meth:`sync_alerts` to replicate alert history.
- Implement :meth:`sync_watchlist` to replicate the watchlist.
- Add incremental sync with a ``last_synced_at`` watermark.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SupabaseClient:
    """
    Client for Supabase cloud database synchronisation.

    Parameters
    ----------
    url:
        Supabase project URL (e.g. ``https://xyzcompany.supabase.co``).
    key:
        Supabase anon or service role key.

    Notes
    -----
    Instantiation does NOT establish a connection in Sprint 1. A call to
    :meth:`connect` will be needed in Sprint 4.
    """

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key
        self._connected: bool = False

    async def connect(self) -> None:
        """
        Establish a connection to Supabase.

        Sprint 1: no-op placeholder.
        Sprint 4: initialise the supabase-py client and verify connectivity.
        """
        logger.info(
            "SupabaseClient.connect() called — no-op in Sprint 1. "
            "Full implementation due in Sprint 4."
        )

    async def disconnect(self) -> None:
        """
        Close the Supabase connection.

        Sprint 1: no-op placeholder.
        """
        logger.info("SupabaseClient.disconnect() called — no-op in Sprint 1.")

    async def sync_tokens(self) -> None:
        """
        Synchronise the local tokens table to Supabase.

        Sprint 1: no-op placeholder.
        Sprint 4: upsert all token rows modified since last sync.
        """
        logger.debug("sync_tokens() called — no-op in Sprint 1.")

    async def sync_alerts(self) -> None:
        """
        Replicate the local alerts table to Supabase.

        Sprint 1: no-op placeholder.
        """
        logger.debug("sync_alerts() called — no-op in Sprint 1.")

    async def sync_watchlist(self) -> None:
        """
        Replicate the local watchlist table to Supabase.

        Sprint 1: no-op placeholder.
        """
        logger.debug("sync_watchlist() called — no-op in Sprint 1.")

    @property
    def is_connected(self) -> bool:
        """True when the client has an active Supabase connection."""
        return self._connected

    def info(self) -> dict[str, object]:
        """
        Return a status summary for logging and the /health endpoint.

        Returns
        -------
        dict
            Keys: ``connected`` (bool), ``url`` (str, redacted).
        """
        redacted_url = self._url[:30] + "..." if len(self._url) > 30 else self._url
        return {
            "connected": self._connected,
            "url": redacted_url,
        }
