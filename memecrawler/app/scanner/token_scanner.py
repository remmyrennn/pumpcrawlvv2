"""
Token Scanner.

Orchestrates the end-to-end scan cycle:

1. **Discovery** — fetch new token candidates from all providers.
2. **Intake** — add valid candidates to the watchlist.
3. **Scan queue** — retrieve tokens due for a scan, ordered by priority
   (CRITICAL → HIGH → MEDIUM → LOW).
4. **Per-token scan** — fetch up-to-date market data from DexScreener
   (primary) or Pump.fun (fallback); store results; evaluate priority and
   state transitions.

Provider failures never crash the scanner. Each token scan is wrapped in
its own try/except; a failure updates error counters but does not block
the next token in the queue.

Sprint 2 does NOT dispatch production alerts. State transitions up to
HIGH_PRIORITY are supported; READY_FOR_ALERT and beyond are Sprint 3.

Sprint 2.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.cache.manager import CacheManager
from app.config.settings import Settings
from app.discovery.engine import DiscoveryEngine
from app.models.token import ScanPriority, TokenData, TokenState, WatchEntry
from app.providers.dexscreener import DexScreenerProvider
from app.providers.manager import ProviderManager
from app.scanner.watchlist import WatchlistManager
from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)


class TokenScanner:
    """
    Drives the full discovery and scan cycle.

    Intended to be registered as a recurring job with the
    :class:`~app.scanner.scheduler.Scheduler`.

    Parameters
    ----------
    provider_manager:
        Registry of all active providers.
    watchlist:
        Watchlist manager for reads and writes.
    discovery:
        Discovery engine that fetches and filters new candidates.
    cache:
        In-process cache for duplicate-scan prevention.
    settings:
        Application configuration (thresholds, feature flags).
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        watchlist: WatchlistManager,
        discovery: DiscoveryEngine,
        cache: CacheManager,
        settings: Settings,
    ) -> None:
        self._providers = provider_manager
        self._watchlist = watchlist
        self._discovery = discovery
        self._cache = cache
        self._settings = settings

        self._cycles: int = 0
        self._tokens_scanned: int = 0
        self._scan_errors: int = 0

    # ── Main entry point ───────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        """
        Execute one full scan cycle (discovery + watchlist scan).

        This is the coroutine registered with the Scheduler.  Each call
        is a discrete, self-contained cycle; failures in one cycle do not
        affect the next.
        """
        self._cycles += 1
        cycle_start = utcnow_iso()
        logger.info("Scanner cycle #%d started at %s.", self._cycles, cycle_start)

        # ── Step 1: Discovery ──────────────────────────────────────────────
        await self._run_discovery()

        # ── Step 2: Scan watchlist ─────────────────────────────────────────
        await self._run_watchlist_scan()

        logger.info("Scanner cycle #%d complete.", self._cycles)

    # ── Discovery step ─────────────────────────────────────────────────────

    async def _run_discovery(self) -> None:
        """Fetch new candidates and add them to the watchlist."""
        try:
            candidates = await self._discovery.discover()
        except Exception as exc:
            logger.error("Discovery step failed: %s", exc)
            return

        added = 0
        for token in candidates:
            # Skip cache prevents hammering the DB for tokens we just saw
            if self._cache.has(f"discovery:skip:{token.mint}"):
                continue
            try:
                was_added = await self._watchlist.add_token(
                    token, reason="auto_discovery"
                )
                if was_added:
                    added += 1
                    # After adding, immediately advance to VALIDATED if we have
                    # enough data; otherwise leave as DISCOVERED.
                    await self._try_validate(token)
                else:
                    # Already known — cache to avoid future DB round-trips
                    self._cache.set(
                        f"discovery:skip:{token.mint}", True, ttl=300.0
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to add token %s to watchlist: %s", token.mint[:12], exc
                )

        if added:
            logger.info("Discovery: %d new tokens added to watchlist.", added)

    async def _try_validate(self, token: TokenData) -> None:
        """
        Attempt to advance a freshly discovered token to VALIDATED.

        A token is VALIDATED when it has a valid mint and passes basic
        checks. The full quality check (liquidity/volume thresholds) is
        done during the first scan.
        """
        try:
            await self._watchlist.transition_state(
                token.mint, TokenState.VALIDATED
            )
        except Exception as exc:
            logger.debug(
                "Could not validate %s at discovery time: %s", token.mint[:12], exc
            )

    # ── Watchlist scan step ────────────────────────────────────────────────

    async def _run_watchlist_scan(self) -> None:
        """Scan all tokens that are due, in priority order."""
        try:
            due = await self._watchlist.get_due_tokens()
        except Exception as exc:
            logger.error("Failed to retrieve due tokens: %s", exc)
            return

        if not due:
            logger.debug("No tokens due for scanning.")
            return

        logger.info("Scanner: %d token(s) due for scanning.", len(due))

        # Scan each token sequentially (avoids provider rate-limit hammering).
        # A small concurrency limit could be introduced in Sprint 3 if needed.
        for entry in due:
            lock_key = f"scanning:{entry.mint}"
            if self._cache.has(lock_key):
                # Another cycle is already scanning this token (shouldn't
                # happen with a single-threaded scheduler but defensive).
                continue
            self._cache.set(lock_key, True, ttl=30.0)
            try:
                await self._scan_token(entry)
            except Exception as exc:
                self._scan_errors += 1
                logger.exception(
                    "Unhandled error scanning %s: %s", entry.mint[:12], exc
                )
            finally:
                self._cache.delete(lock_key)

    # ── Per-token scan ─────────────────────────────────────────────────────

    async def _scan_token(self, entry: WatchEntry) -> None:
        """
        Scan a single token: fetch data, record, evaluate priority and state.

        Parameters
        ----------
        entry:
            The watchlist entry to scan.
        """
        self._tokens_scanned += 1

        # Fetch market data from the best available provider
        token_data = await self._fetch_token_data(entry.mint)

        # Record the scan (updates watchlist row + inserts history)
        await self._watchlist.record_scan(entry.mint, token_data)

        if token_data is None:
            logger.debug("No data returned for %s — scan recorded.", entry.mint[:12])
            return

        # ── Priority evaluation ────────────────────────────────────────────
        new_priority = self._evaluate_priority(token_data)
        if new_priority != entry.priority:
            try:
                await self._watchlist.update_priority(entry.mint, new_priority)
            except Exception as exc:
                logger.warning(
                    "Priority update failed for %s: %s", entry.mint[:12], exc
                )

        # ── State evaluation ───────────────────────────────────────────────
        new_state = self._evaluate_state(entry, token_data)
        if new_state and new_state != entry.state:
            try:
                await self._watchlist.transition_state(entry.mint, new_state)
            except Exception as exc:
                logger.warning(
                    "State transition failed for %s: %s", entry.mint[:12], exc
                )

    # ── Market data fetch ──────────────────────────────────────────────────

    async def _fetch_token_data(self, mint: str) -> Optional[TokenData]:
        """
        Fetch the most current market data for a token.

        Tries DexScreener first (primary); falls back to Pump.fun when
        DexScreener is unavailable or returns no data. Returns None when
        all providers fail.
        """
        # DexScreener primary
        try:
            dex: DexScreenerProvider = self._providers.get("dexscreener")  # type: ignore[assignment]
            data = await dex.get_token_data(mint)
            if data is not None:
                return data
        except Exception as exc:
            logger.debug("DexScreener fetch failed for %s: %s", mint[:12], exc)

        # Pump.fun fallback
        try:
            pf = self._providers.get("pumpfun")
            from app.providers.pumpfun import PumpFunProvider
            if isinstance(pf, PumpFunProvider):
                data = await pf.get_token_data(mint)
                if data is not None:
                    return data
        except Exception as exc:
            logger.debug("Pump.fun fallback fetch failed for %s: %s", mint[:12], exc)

        return None

    # ── Priority evaluation ────────────────────────────────────────────────

    def _evaluate_priority(self, token: TokenData) -> ScanPriority:
        """
        Assign a scan priority tier based on token market metrics.

        Thresholds are configurable via :class:`~app.config.settings.Settings`.

        Returns
        -------
        ScanPriority
            The appropriate tier for this token's current metrics.
        """
        cfg = self._settings
        vol = token.volume_24h_usd or 0.0
        liq = token.liquidity_usd or 0.0

        if vol >= cfg.priority_critical_volume and liq >= cfg.priority_critical_liquidity:
            return ScanPriority.CRITICAL
        if vol >= cfg.priority_high_volume and liq >= cfg.priority_high_liquidity:
            return ScanPriority.HIGH
        if vol >= cfg.priority_medium_volume and liq >= cfg.priority_medium_liquidity:
            return ScanPriority.MEDIUM
        return ScanPriority.LOW

    # ── State evaluation ───────────────────────────────────────────────────

    def _evaluate_state(
        self,
        entry: WatchEntry,
        token: TokenData,
    ) -> Optional[TokenState]:
        """
        Determine whether the token should transition to a new state.

        Sprint 2 covers transitions from VALIDATED through HIGH_PRIORITY.
        READY_FOR_ALERT (alert dispatch) is Sprint 3.

        Returns
        -------
        TokenState | None
            Target state if a transition is warranted; None if no change.
        """
        cfg = self._settings
        vol = token.volume_24h_usd or 0.0
        liq = token.liquidity_usd or 0.0
        current = entry.state

        # VALIDATED → WATCHING: token has any liquidity data
        if current == TokenState.VALIDATED and liq > 0:
            return TokenState.WATCHING

        # WATCHING → ACCUMULATING: volume crosses medium threshold
        if (
            current == TokenState.WATCHING
            and vol >= cfg.priority_medium_volume
            and liq >= cfg.priority_medium_liquidity
        ):
            return TokenState.ACCUMULATING

        # ACCUMULATING → HIGH_PRIORITY: volume crosses high threshold
        if (
            current == TokenState.ACCUMULATING
            and vol >= cfg.priority_high_volume
            and liq >= cfg.priority_high_liquidity
        ):
            return TokenState.HIGH_PRIORITY

        # ACCUMULATING → WATCHING: momentum lost
        if (
            current == TokenState.ACCUMULATING
            and vol < cfg.priority_medium_volume
            and entry.scan_count > 5
        ):
            return TokenState.WATCHING

        # HIGH_PRIORITY → ACCUMULATING: metrics weakened
        if (
            current == TokenState.HIGH_PRIORITY
            and vol < cfg.priority_high_volume
        ):
            return TokenState.ACCUMULATING

        return None

    # ── Diagnostics ────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """Return runtime diagnostics for the /diagnostics endpoint."""
        return {
            "cycles": self._cycles,
            "tokens_scanned": self._tokens_scanned,
            "scan_errors": self._scan_errors,
        }
