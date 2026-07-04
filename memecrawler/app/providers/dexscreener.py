"""
DexScreener provider.

Provides token pair data, price, volume, and liquidity information from the
DexScreener public API (https://api.dexscreener.com).

No API key required.

Sprint 1: health check only.
Sprint 2: new-token discovery, per-token market data fetch.
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

_BASE_URL = "https://api.dexscreener.com"
_HEALTH_URL = f"{_BASE_URL}/latest/dex/tokens/So11111111111111111111111111111112"
_PROFILES_URL = f"{_BASE_URL}/token-profiles/latest/v1"
_TOKEN_URL = f"{_BASE_URL}/latest/dex/tokens"


class DexScreenerProvider(BaseProvider):
    """
    Provider for DexScreener market data.

    DexScreener does not require an API key for the public endpoints used
    by MemeCrawler. Rate limiting is handled by the shared HTTP client's
    connection pool.

    Attributes
    ----------
    name:
        Registry key used by :class:`~app.providers.manager.ProviderManager`.
    """

    name = "dexscreener"
    version = "2.0.0"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        super().__init__(http_client)

    # ── Health ─────────────────────────────────────────────────────────────

    async def health_check(self) -> ProviderStatus:
        """
        Verify connectivity to the DexScreener API.

        Sends a lightweight GET to a known stable token endpoint and checks
        for a 200 response. Measures round-trip latency.

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
                logger.debug("DexScreener health check passed (%.0f ms).", latency)
            else:
                raise ValueError(f"Unexpected status code: {response.status_code}")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("DexScreener health check failed: %s", exc)

        return self._status

    # ── Discovery ──────────────────────────────────────────────────────────

    async def get_new_tokens(self, limit: int = 50) -> list[TokenData]:
        """
        Fetch the most recently listed Solana token profiles from DexScreener.

        Uses the ``/token-profiles/latest/v1`` endpoint which returns a
        rolling list of the newest token profiles across all chains.
        Only Solana tokens are returned.

        Parameters
        ----------
        limit:
            Maximum number of Solana tokens to return (applied after
            chain-filtering the API response).

        Returns
        -------
        list[TokenData]
            Partial ``TokenData`` records (mint + chain only — no market
            data). Callers must call :meth:`get_token_data` for full data.
        """
        t0 = time.monotonic()
        try:
            response = await self._client.get(_PROFILES_URL, timeout=15.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"Unexpected status code: {response.status_code}")

            raw: list[dict] = response.json() if isinstance(response.json(), list) else []
            self._record_success(latency_ms=latency)

            results: list[TokenData] = []
            seen: set[str] = set()
            for item in raw:
                if item.get("chainId") != "solana":
                    continue
                mint = (item.get("tokenAddress") or "").strip()
                if not mint or mint in seen:
                    continue
                seen.add(mint)
                results.append(TokenData(
                    mint=mint,
                    chain="solana",
                    provider=self.name,
                    fetched_at=utcnow(),
                ))
                if len(results) >= limit:
                    break

            logger.debug(
                "DexScreener: discovered %d new Solana token profiles.", len(results)
            )
            return results

        except Exception as exc:
            self._record_error(exc)
            logger.warning("DexScreener new-token discovery failed: %s", exc)
            return []

    # ── Per-token data ─────────────────────────────────────────────────────

    async def get_token_data(self, mint: str) -> Optional[TokenData]:
        """
        Fetch full market data for a specific Solana token mint address.

        Selects the Solana pair with the highest USD liquidity when multiple
        pairs exist for the same token.

        Parameters
        ----------
        mint:
            Solana token mint address (base-58).

        Returns
        -------
        TokenData | None
            Populated ``TokenData`` on success, or ``None`` when the token
            is not found or the request fails.
        """
        url = f"{_TOKEN_URL}/{mint}"
        t0 = time.monotonic()
        try:
            response = await self._client.get(url, timeout=10.0)
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"Unexpected status code: {response.status_code}")

            data = response.json()
            pairs: list[dict] = data.get("pairs") or []
            solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
            if not solana_pairs:
                return None

            # Pick the pair with the highest liquidity
            pair = max(
                solana_pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
            )
            self._record_success(latency_ms=latency)
            return _parse_pair(pair, mint, self.name)

        except Exception as exc:
            self._record_error(exc)
            logger.warning("DexScreener token-data fetch failed for %s: %s", mint[:12], exc)
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_pair(pair: dict, mint: str, provider: str) -> TokenData:
    """Convert a DexScreener pair dict into a :class:`TokenData`."""
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    liquidity = pair.get("liquidity") or {}
    txns = pair.get("txns") or {}
    txns_5m = txns.get("m5") or {}
    txns_1h = txns.get("h1") or {}

    # Age from pair creation timestamp (milliseconds → seconds)
    pair_created_ms = pair.get("pairCreatedAt")
    age_seconds: Optional[float] = None
    if pair_created_ms:
        try:
            age_seconds = utcnow_ts() - int(pair_created_ms) / 1000
        except (TypeError, ValueError):
            pass

    def _float(v: object) -> Optional[float]:
        try:
            result = float(v)  # type: ignore[arg-type]
            return result if result > 0 else None
        except (TypeError, ValueError):
            return None

    def _int(v: object) -> Optional[int]:
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return TokenData(
        mint=mint,
        symbol=base.get("symbol", ""),
        name=base.get("name", ""),
        chain="solana",
        price_usd=_float(pair.get("priceUsd")),
        market_cap_usd=_float(pair.get("marketCap") or pair.get("fdv")),
        volume_24h_usd=_float(volume.get("h24")),
        liquidity_usd=_float(liquidity.get("usd")),
        buys_5m=_int(txns_5m.get("buys")),
        sells_5m=_int(txns_5m.get("sells")),
        buys_1h=_int(txns_1h.get("buys")),
        sells_1h=_int(txns_1h.get("sells")),
        age_seconds=age_seconds,
        pair_address=pair.get("pairAddress"),
        provider=provider,
        fetched_at=utcnow(),
    )
