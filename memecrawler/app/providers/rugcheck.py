"""
RugCheck provider.

Provides token safety scores, risk flags, and rug-pull probability
assessments from the RugCheck API (https://api.rugcheck.xyz).

No API key required for public endpoints.

Sprint 1: health check only.
Sprint 2: token safety report fetch.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.providers.base import BaseProvider, ProviderStatus

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.rugcheck.xyz/v1"
# SOL native mint — always has a RugCheck report and responds quickly.
_HEALTH_URL = f"{_BASE_URL}/tokens/So11111111111111111111111111111112/report/summary"


class RugCheckProvider(BaseProvider):
    """
    Provider for RugCheck token safety data.

    No API key is required for public endpoints.

    Attributes
    ----------
    name:
        Registry key used by :class:`~app.providers.manager.ProviderManager`.
    """

    name = "rugcheck"
    version = "2.0.0"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        super().__init__(http_client)

    # ── Health ─────────────────────────────────────────────────────────────

    async def health_check(self) -> ProviderStatus:
        """
        Verify connectivity to the RugCheck API.

        Probes the summary report for the SOL native mint (always available).

        Returns
        -------
        ProviderStatus
            HEALTHY on success, DEGRADED/DOWN on error.
        """
        t0 = time.monotonic()
        try:
            response = await self._client.get(_HEALTH_URL, timeout=10.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code == 200:
                self._record_success(latency_ms=latency)
                logger.debug("RugCheck health check passed (%.0f ms).", latency)
            else:
                raise ValueError(f"Unexpected status code: {response.status_code}")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("RugCheck health check failed: %s", exc)

        return self._status

    # ── Token report ───────────────────────────────────────────────────────

    async def get_token_report(self, mint: str) -> Optional[dict[str, Any]]:
        """
        Fetch the full safety report for a token from RugCheck.

        The report includes a risk score (0–1000; lower = safer), a list of
        risk flags, and token metadata. It is intended for use by the
        validation step of the discovery engine.

        Parameters
        ----------
        mint:
            Solana token mint address (base-58).

        Returns
        -------
        dict | None
            The raw RugCheck report dict on success. ``None`` when the token
            is unknown to RugCheck or the request fails.
        """
        url = f"{_BASE_URL}/tokens/{mint}/report"
        t0 = time.monotonic()
        try:
            response = await self._client.get(url, timeout=15.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code == 200:
                self._record_success(latency_ms=latency)
                logger.debug(
                    "RugCheck report fetched for %s (%.0f ms).", mint[:12], latency
                )
                return response.json()
            if response.status_code == 404:
                logger.debug("RugCheck: no report found for %s.", mint[:12])
                return None
            raise ValueError(f"Unexpected status code: {response.status_code}")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("RugCheck report fetch failed for %s: %s", mint[:12], exc)
            return None

    async def get_token_summary(self, mint: str) -> Optional[dict[str, Any]]:
        """
        Fetch the lightweight summary report for a token.

        Faster than :meth:`get_token_report`; use for quick risk checks
        during discovery validation.

        Parameters
        ----------
        mint:
            Solana token mint address.

        Returns
        -------
        dict | None
            The summary dict, or ``None`` on failure.
        """
        url = f"{_BASE_URL}/tokens/{mint}/report/summary"
        t0 = time.monotonic()
        try:
            response = await self._client.get(url, timeout=10.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code == 200:
                self._record_success(latency_ms=latency)
                return response.json()
            if response.status_code == 404:
                return None
            raise ValueError(f"Unexpected status code: {response.status_code}")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("RugCheck summary fetch failed for %s: %s", mint[:12], exc)
            return None
