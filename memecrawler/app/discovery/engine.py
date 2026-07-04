"""
Discovery Engine.

Fetches newly listed Solana tokens from all configured providers,
merges results, deduplicates by mint address, and applies a filter
pipeline to reject tokens that do not meet minimum quality thresholds.

Only valid, deduplicated candidates are returned to the caller. The
caller (``TokenScanner``) decides whether to add them to the watchlist.

Sprint 2.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.models.token import TokenData
from app.utils.validation import is_valid_token_mint

if TYPE_CHECKING:
    from app.providers.dexscreener import DexScreenerProvider
    from app.providers.pumpfun import PumpFunProvider

logger = logging.getLogger(__name__)


class DiscoveryEngine:
    """
    Merges and filters new-token candidates from multiple providers.

    Parameters
    ----------
    dexscreener:
        DexScreener provider instance (may be None if disabled).
    pumpfun:
        Pump.fun provider instance (may be None if disabled/unavailable).
    min_liquidity_usd:
        Minimum USD liquidity a token must have to pass the filter.
        Set to 0 to disable the liquidity filter.
    blacklisted_tokens:
        Frozenset of mint addresses that are permanently rejected.
    blacklisted_developers:
        Frozenset of developer wallet addresses that are permanently
        rejected (not yet enforced in Sprint 2 — placeholder for Sprint 3).
    discovery_limit:
        Maximum tokens to request per provider per cycle.
    """

    def __init__(
        self,
        dexscreener: "DexScreenerProvider | None" = None,
        pumpfun: "PumpFunProvider | None" = None,
        *,
        min_liquidity_usd: float = 500.0,
        blacklisted_tokens: frozenset[str] = frozenset(),
        blacklisted_developers: frozenset[str] = frozenset(),
        discovery_limit: int = 50,
    ) -> None:
        self._dexscreener = dexscreener
        self._pumpfun = pumpfun
        self._min_liquidity = min_liquidity_usd
        self._blacklisted_tokens = blacklisted_tokens
        self._blacklisted_developers = blacklisted_developers
        self._limit = discovery_limit

        # Running counters for diagnostics
        self._total_discovered: int = 0
        self._total_rejected: int = 0
        self._cycles: int = 0

    # ── Public API ─────────────────────────────────────────────────────────

    async def discover(self) -> list[TokenData]:
        """
        Run one discovery cycle across all configured providers.

        Fetches new tokens from each provider concurrently, merges the
        results, deduplicates by mint, and applies the rejection filter.

        Returns
        -------
        list[TokenData]
            Validated, deduplicated candidates ready for the watchlist.
        """
        self._cycles += 1
        logger.info("Discovery cycle #%d starting.", self._cycles)

        # Collect from all providers concurrently
        tasks: list[asyncio.Task[list[TokenData]]] = []

        if self._dexscreener is not None:
            tasks.append(asyncio.create_task(
                self._fetch_dexscreener(), name="discovery:dexscreener"
            ))
        if self._pumpfun is not None:
            tasks.append(asyncio.create_task(
                self._fetch_pumpfun(), name="discovery:pumpfun"
            ))

        if not tasks:
            logger.warning("Discovery: no providers available — skipping cycle.")
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        raw: list[TokenData] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Discovery task failed: %s", result)
            else:
                raw.extend(result)

        self._total_discovered += len(raw)
        logger.debug("Discovery: fetched %d raw candidates.", len(raw))

        # Deduplicate + filter
        candidates = self._deduplicate(raw)
        valid = self._filter(candidates)

        rejected = len(candidates) - len(valid)
        self._total_rejected += rejected
        logger.info(
            "Discovery cycle #%d complete: %d fetched, %d deduplicated, "
            "%d rejected, %d valid.",
            self._cycles,
            len(raw),
            len(candidates),
            rejected,
            len(valid),
        )
        return valid

    # ── Provider fetchers ─────────────────────────────────────────────────

    async def _fetch_dexscreener(self) -> list[TokenData]:
        """Fetch new tokens from DexScreener. Never raises."""
        if self._dexscreener is None:
            return []
        try:
            return await self._dexscreener.get_new_tokens(limit=self._limit)
        except Exception as exc:
            logger.error("DexScreener discovery fetch error: %s", exc)
            return []

    async def _fetch_pumpfun(self) -> list[TokenData]:
        """Fetch new tokens from Pump.fun. Never raises."""
        if self._pumpfun is None:
            return []
        try:
            return await self._pumpfun.get_new_coins(limit=self._limit)
        except Exception as exc:
            logger.error("Pump.fun discovery fetch error: %s", exc)
            return []

    # ── Deduplication ──────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(tokens: list[TokenData]) -> list[TokenData]:
        """
        Remove duplicate mints, keeping the first occurrence.

        When the same mint is returned by multiple providers, the first
        entry (ordered by provider fetch) is kept and the rest discarded.
        """
        seen: set[str] = set()
        unique: list[TokenData] = []
        for token in tokens:
            if token.mint and token.mint not in seen:
                seen.add(token.mint)
                unique.append(token)
        return unique

    # ── Filter pipeline ────────────────────────────────────────────────────

    def _filter(self, tokens: list[TokenData]) -> list[TokenData]:
        """
        Apply the rejection filter pipeline to a deduplicated list.

        A token is rejected if any of the following conditions are true:

        1. **Missing contract** — mint is empty or None.
        2. **Invalid address** — mint fails the base-58 structural check.
        3. **Wrong chain** — chain is not ``"solana"``.
        4. **Blacklisted token** — mint is in the blacklist.
        5. **Missing liquidity** — ``liquidity_usd`` is set and below the
           configured minimum (tokens with ``None`` liquidity pass — their
           liquidity will be fetched during the scan step).
        """
        valid: list[TokenData] = []
        for token in tokens:
            reason = self._reject_reason(token)
            if reason:
                logger.debug(
                    "Discovery rejected %s (%s): %s",
                    token.symbol or "?",
                    (token.mint or "?")[:12],
                    reason,
                )
            else:
                valid.append(token)
        return valid

    def _reject_reason(self, token: TokenData) -> str | None:
        """
        Return a rejection reason string, or None when the token is valid.

        Parameters
        ----------
        token:
            The candidate to evaluate.

        Returns
        -------
        str | None
            Human-readable rejection reason, or None if the token passes.
        """
        if not token.mint:
            return "missing contract"
        if not is_valid_token_mint(token.mint):
            return "invalid address format"
        if token.chain and token.chain != "solana":
            return f"wrong chain: {token.chain}"
        if token.mint in self._blacklisted_tokens:
            return "blacklisted token"
        # Only apply liquidity filter when the data is present.
        # Tokens fetched from profile endpoints lack liquidity data;
        # it will be populated when the full token scan runs.
        if (
            token.liquidity_usd is not None
            and self._min_liquidity > 0
            and token.liquidity_usd < self._min_liquidity
        ):
            return f"liquidity too low: ${token.liquidity_usd:.0f}"
        return None

    # ── Diagnostics ────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """Return diagnostic stats for the /diagnostics endpoint."""
        return {
            "cycles": self._cycles,
            "total_discovered": self._total_discovered,
            "total_rejected": self._total_rejected,
            "providers": [
                p for p in ["dexscreener", "pumpfun"]
                if getattr(self, f"_{'dexscreener' if p == 'dexscreener' else 'pumpfun'}")
                is not None
            ],
        }
