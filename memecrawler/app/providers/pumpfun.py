"""
Pump.fun provider.

Provides newly launched memecoin data from the Pump.fun platform.
Pump.fun is the primary source for discovering early-stage Solana tokens.

No API key required.

Important: The Pump.fun frontend API (frontend-api.pump.fun) is protected
by Cloudflare and returns HTTP 530 from Replit/cloud IPs. All public fetch
methods handle this gracefully and return empty results rather than raising
so that the scanner continues without interruption.

Sprint 1: health check only.
Sprint 2: new-token discovery, per-token data fetch (with Cloudflare caveat).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.models.token import TokenData
from app.providers.base import BaseProvider, ProviderStatus
from app.utils.time_utils import utcnow, utcnow_ts

logger = logging.getLogger(__name__)

_BASE_URL = "https://frontend-api.pump.fun"
_COINS_URL = f"{_BASE_URL}/coins"
_HEALTH_URL = (
    f"{_BASE_URL}/coins?limit=1&offset=0"
    "&sort=last_trade_timestamp&order=DESC&includeNsfw=false"
)


class PumpFunProvider(BaseProvider):
    """
    Provider for Pump.fun newly launched token data.

    No API key is required. The public frontend API is used directly.

    Attributes
    ----------
    name:
        Registry key used by :class:`~app.providers.manager.ProviderManager`.
    """

    name = "pumpfun"
    version = "2.0.0"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        super().__init__(http_client)

    # ── Health ─────────────────────────────────────────────────────────────

    async def health_check(self) -> ProviderStatus:
        """
        Verify connectivity to the Pump.fun frontend API.

        Returns DEGRADED (not DOWN) on Cloudflare 530 since the service
        itself is functional — it is the Replit IP that is blocked.

        Returns
        -------
        ProviderStatus
            HEALTHY on success, DEGRADED on 5xx/Cloudflare, DOWN after 3 errors.
        """
        t0 = time.monotonic()
        try:
            response = await self._client.get(_HEALTH_URL, timeout=10.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code == 200:
                self._record_success(latency_ms=latency)
                logger.debug("Pump.fun health check passed (%.0f ms).", latency)
            else:
                raise ValueError(f"Unexpected status code: {response.status_code}")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("Pump.fun health check failed: %s", exc)

        return self._status

    # ── Discovery ──────────────────────────────────────────────────────────

    async def get_new_coins(self, limit: int = 50) -> list[TokenData]:
        """
        Fetch the most recently traded coins from Pump.fun.

        Parameters
        ----------
        limit:
            Maximum number of coins to return (capped at 50 per API call).

        Returns
        -------
        list[TokenData]
            List of token data. Empty when the API is unreachable (e.g.
            Cloudflare blocks the request from the current IP).
        """
        params = {
            "limit": min(limit, 50),
            "offset": 0,
            "sort": "last_trade_timestamp",
            "order": "DESC",
            "includeNsfw": "false",
        }
        t0 = time.monotonic()
        try:
            response = await self._client.get(_COINS_URL, params=params, timeout=15.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"Unexpected status code: {response.status_code}")

            raw: list[dict] = response.json() if isinstance(response.json(), list) else []
            self._record_success(latency_ms=latency)

            results: list[TokenData] = []
            seen: set[str] = set()
            for coin in raw:
                mint = (coin.get("mint") or "").strip()
                if not mint or mint in seen:
                    continue
                seen.add(mint)
                results.append(_parse_coin(coin, self.name))

            logger.debug("Pump.fun: discovered %d new coins.", len(results))
            return results

        except Exception as exc:
            self._record_error(exc)
            logger.warning("Pump.fun coin discovery failed (expected on cloud IPs): %s", exc)
            return []

    # ── Per-token data ─────────────────────────────────────────────────────

    async def get_token_data(self, mint: str) -> Optional[TokenData]:
        """
        Fetch Pump.fun metadata for a specific token mint.

        Parameters
        ----------
        mint:
            Solana token mint address.

        Returns
        -------
        TokenData | None
            Populated data on success, or ``None`` when unreachable.
        """
        url = f"{_COINS_URL}/{mint}"
        t0 = time.monotonic()
        try:
            response = await self._client.get(url, timeout=10.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"Unexpected status code: {response.status_code}")
            coin = response.json()
            self._record_success(latency_ms=latency)
            return _parse_coin(coin, self.name)
        except Exception as exc:
            self._record_error(exc)
            logger.warning("Pump.fun token-data fetch failed for %s: %s", mint[:12], exc)
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_coin(coin: dict, provider: str) -> TokenData:
    """Convert a Pump.fun coin dict into a :class:`TokenData`."""

    def _float(v: object) -> Optional[float]:
        try:
            result = float(v)  # type: ignore[arg-type]
            return result if result > 0 else None
        except (TypeError, ValueError):
            return None

    # Pump.fun returns market_cap in SOL; also exposes usd_market_cap
    market_cap = _float(coin.get("usd_market_cap"))

    # created_timestamp is POSIX seconds
    created_ts = coin.get("created_timestamp")
    age_seconds: Optional[float] = None
    if created_ts:
        try:
            age_seconds = utcnow_ts() - float(created_ts)
        except (TypeError, ValueError):
            pass

    return TokenData(
        mint=(coin.get("mint") or "").strip(),
        symbol=(coin.get("symbol") or "").strip(),
        name=(coin.get("name") or "").strip(),
        chain="solana",
        market_cap_usd=market_cap,
        age_seconds=age_seconds,
        provider=provider,
        fetched_at=utcnow(),
    )
