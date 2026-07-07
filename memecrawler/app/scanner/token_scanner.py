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
5. **Intelligence** (Sprint 3) — score, rank, check alert eligibility,
   dispatch alerts, track milestones for TRACKING tokens.

Provider failures never crash the scanner. Each token scan is wrapped in
its own try/except; a failure updates error counters but does not block
the next token in the queue.

Sprint 3.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

from app.cache.manager import CacheManager
from app.config.settings import Settings
from app.discovery.engine import DiscoveryEngine
from app.models.token import ScanPriority, TokenData, TokenState, WatchEntry
from app.providers.dexscreener import DexScreenerProvider
from app.providers.manager import ProviderManager
from app.scanner.watchlist import WatchlistManager
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.analysis.alert_engine import AlertEngine
    from app.analysis.market_mode import MarketModeDetector
    from app.analysis.milestone import MilestoneTracker
    from app.analysis.ranking import RankingEngine
    from app.analysis.scorer import ScoringEngine
    from app.database.manager import DatabaseManager
    from app.providers.rugcheck import RugCheckProvider

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
        In-process cache for duplicate-scan prevention and RugCheck TTL.
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

        # Sprint 3 timing (for avg scan time heartbeat metric)
        self._total_scan_ms: float = 0.0
        self._scan_count_for_avg: int = 0

        # Sprint 3 intelligence components (injected after construction)
        self._scorer: Optional["ScoringEngine"] = None
        self._alert_engine: Optional["AlertEngine"] = None
        self._ranking: Optional["RankingEngine"] = None
        self._milestone: Optional["MilestoneTracker"] = None
        self._market_mode: Optional["MarketModeDetector"] = None
        self._db: Optional["DatabaseManager"] = None
        self._rugcheck: Optional["RugCheckProvider"] = None

    # ── Sprint 3: Intelligence injection ──────────────────────────────────

    def set_intelligence_context(
        self,
        *,
        scorer: Optional["ScoringEngine"] = None,
        alert_engine: Optional["AlertEngine"] = None,
        ranking: Optional["RankingEngine"] = None,
        milestone: Optional["MilestoneTracker"] = None,
        market_mode: Optional["MarketModeDetector"] = None,
        db: Optional["DatabaseManager"] = None,
        rugcheck: Optional["RugCheckProvider"] = None,
    ) -> None:
        """
        Inject Sprint 3 intelligence components.

        Called from the lifespan handler after all singletons are created.
        """
        self._scorer = scorer
        self._alert_engine = alert_engine
        self._ranking = ranking
        self._milestone = milestone
        self._market_mode = market_mode
        self._db = db
        self._rugcheck = rugcheck

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

        # Step 1: Discovery
        await self._run_discovery()

        # Step 2: Refresh market mode (Sprint 3)
        if self._market_mode is not None:
            try:
                await self._market_mode.refresh()
            except Exception as exc:
                logger.warning("Market mode refresh failed: %s", exc)

        # Step 3: Scan watchlist (active tokens)
        await self._run_watchlist_scan()

        # Step 4: Milestone scans for TRACKING tokens
        await self._run_tracking_milestones()

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
            if self._cache.has(f"discovery:skip:{token.mint}"):
                continue
            try:
                was_added = await self._watchlist.add_token(
                    token, reason="auto_discovery"
                )
                if was_added:
                    added += 1
                    await self._try_validate(token)
                else:
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
        """Attempt to advance a freshly discovered token to VALIDATED."""
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

        for entry in due:
            lock_key = f"scanning:{entry.mint}"
            if self._cache.has(lock_key):
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

    # ── Tracking milestone scan ────────────────────────────────────────────

    async def _run_tracking_milestones(self) -> None:
        """
        Run milestone checks for all TRACKING tokens that are due.

        TRACKING tokens are excluded from the normal :meth:`get_due_tokens`
        queue (they no longer need full-pipeline scoring), but they do need
        periodic market data fetches so MilestoneTracker can fire on
        +25/50/100/ATH/drawdown/rug/death events.
        """
        if self._milestone is None:
            return

        try:
            tracking = await self._watchlist.get_tracking_tokens()
        except Exception as exc:
            logger.error("Failed to retrieve tracking tokens: %s", exc)
            return

        if not tracking:
            return

        logger.debug("Milestone scan: %d TRACKING token(s).", len(tracking))
        for entry in tracking:
            lock_key = f"tracking:{entry.mint}"
            if self._cache.has(lock_key):
                continue
            self._cache.set(lock_key, True, ttl=30.0)
            try:
                token_data = await self._fetch_token_data(entry.mint)
                if token_data is not None:
                    await self._milestone.check(
                        mint=entry.mint,
                        symbol=token_data.symbol or "",
                        current=token_data,
                    )
                # Advance next_scan_at even on no-data so we don't retry immediately
                await self._watchlist.record_scan(entry.mint, token_data)
            except Exception as exc:
                logger.warning(
                    "Milestone scan failed for %s: %s", entry.mint[:12], exc
                )
            finally:
                self._cache.delete(lock_key)

    # ── Per-token scan ─────────────────────────────────────────────────────

    async def _scan_token(self, entry: WatchEntry) -> None:
        """
        Scan a single token: fetch data, record, evaluate priority, state,
        and run intelligence (scoring, alerts, milestones) in Sprint 3.
        """
        self._tokens_scanned += 1
        scan_start = time.monotonic()

        # Fetch market data from the best available provider
        token_data = await self._fetch_token_data(entry.mint)

        # Record the scan (updates watchlist row + inserts history)
        await self._watchlist.record_scan(entry.mint, token_data)

        if token_data is None:
            logger.debug("No data returned for %s — scan recorded.", entry.mint[:12])
            self._record_scan_time(time.monotonic() - scan_start)
            return

        # ── Sprint 3: Milestone tracking for TRACKING tokens ───────────────
        if entry.state == TokenState.TRACKING and self._milestone is not None:
            try:
                await self._milestone.check(
                    mint=entry.mint,
                    symbol=token_data.symbol or "",
                    current=token_data,
                )
            except Exception as exc:
                logger.warning(
                    "Milestone check failed for %s: %s", entry.mint[:12], exc
                )

        # ── Priority evaluation ────────────────────────────────────────────
        new_priority = self._evaluate_priority(token_data)
        if new_priority != entry.priority:
            try:
                await self._watchlist.update_priority(entry.mint, new_priority)
            except Exception as exc:
                logger.warning(
                    "Priority update failed for %s: %s", entry.mint[:12], exc
                )

        # ── State evaluation (Sprint 2 transitions) ────────────────────────
        new_state = self._evaluate_state(entry, token_data)
        effective_state = entry.state
        if new_state and new_state != entry.state:
            try:
                await self._watchlist.transition_state(entry.mint, new_state)
                effective_state = new_state
            except Exception as exc:
                logger.warning(
                    "State transition failed for %s: %s", entry.mint[:12], exc
                )

        # ── Sprint 3: Intelligence (scoring + alert dispatch) ──────────────
        if (
            self._scorer is not None
            and self._db is not None
            and effective_state not in (TokenState.ARCHIVED, TokenState.TRACKING)
        ):
            try:
                await self._run_intelligence(entry, token_data, effective_state)
            except Exception as exc:
                logger.warning(
                    "Intelligence step failed for %s: %s", entry.mint[:12], exc
                )

        self._record_scan_time(time.monotonic() - scan_start)

    # ── Intelligence step (Sprint 3) ──────────────────────────────────────

    async def _run_intelligence(
        self,
        entry: WatchEntry,
        token_data: TokenData,
        effective_state: TokenState,
    ) -> None:
        """Run scoring, ranking update, and alert dispatch for one token."""
        from app.analysis.models import MarketMode

        # ── RugCheck data (cached, 30-min TTL) ────────────────────────────
        rugcheck_data = await self._fetch_rugcheck_data(entry.mint)

        # ── History and historical scores from DB ──────────────────────────
        history_rows = await self._db.fetchall(  # type: ignore[union-attr]
            """
            SELECT price_usd, market_cap_usd, volume_24h_usd, liquidity_usd,
                   buys_5m, sells_5m, age_seconds, recorded_at, source
            FROM history
            WHERE mint = ?
            ORDER BY recorded_at DESC
            LIMIT 20
            """,
            (entry.mint,),
        )
        history = [dict(r) for r in history_rows]

        hist_score_rows = await self._db.fetchall(  # type: ignore[union-attr]
            """
            SELECT score FROM evaluations
            WHERE mint = ?
            ORDER BY evaluated_at DESC
            LIMIT 10
            """,
            (entry.mint,),
        )
        historical_scores = [float(r["score"]) for r in hist_score_rows]

        # ── Current market mode ────────────────────────────────────────────
        market_mode = (
            self._market_mode.current_mode
            if self._market_mode is not None
            else MarketMode.NEUTRAL
        )

        # ── Run scoring engine ─────────────────────────────────────────────
        evaluation = await self._scorer.evaluate(  # type: ignore[union-attr]
            entry=entry,
            current=token_data,
            history=history,
            rugcheck_data=rugcheck_data,
            historical_scores=historical_scores,
            market_mode=market_mode,
        )

        # ── Persist evaluation ─────────────────────────────────────────────
        row = evaluation.to_db_row()
        await self._db.execute(  # type: ignore[union-attr]
            """
            INSERT INTO evaluations
                (mint, score, max_score, confidence, risk_level,
                 reasons, details, market_mode, scan_count, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["mint"], row["score"], row["max_score"], row["confidence"],
                row["risk_level"], row["reasons"], row["details"],
                row["market_mode"], row["scan_count"], row["evaluated_at"],
            ),
        )

        # ── Update watchlist score/confidence columns ──────────────────────
        await self._db.execute(  # type: ignore[union-attr]
            """
            UPDATE watchlist
            SET score = ?, confidence = ?, risk_level = ?
            WHERE mint = ?
            """,
            (
                round(evaluation.final_score, 2),
                round(evaluation.confidence, 2),
                evaluation.risk_level.value,
                entry.mint,
            ),
        )

        # ── Ranking update ─────────────────────────────────────────────────
        if self._ranking is not None:
            await self._ranking.update(evaluation)
            # Sync symbol into rankings table
            if token_data.symbol:
                await self._db.execute(  # type: ignore[union-attr]
                    "UPDATE rankings SET symbol = ? WHERE mint = ?",
                    (token_data.symbol, entry.mint),
                )

        # ── Alert dispatch ─────────────────────────────────────────────────
        if self._alert_engine is not None and evaluation.eligible_for_alert:
            await self._alert_engine.maybe_alert(
                evaluation=evaluation,
                symbol=token_data.symbol or "",
                name=token_data.name or "",
                current_price_usd=token_data.price_usd,
            )

        logger.debug(
            "Intelligence done: %s  score=%.1f  conf=%.1f%%  risk=%s  eligible=%s",
            entry.mint[:12],
            evaluation.final_score,
            evaluation.confidence,
            evaluation.risk_level.value,
            evaluation.eligible_for_alert,
        )

    # ── RugCheck fetch with cache ──────────────────────────────────────────

    async def _fetch_rugcheck_data(self, mint: str) -> Optional[dict]:
        """Fetch RugCheck report for a mint, with 30-minute in-process cache."""
        cache_key = f"rugcheck:{mint}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        if self._rugcheck is None:
            # Try to get from provider manager as fallback
            try:
                from app.providers.rugcheck import RugCheckProvider
                rc = self._providers.get("rugcheck")
                if isinstance(rc, RugCheckProvider):
                    self._rugcheck = rc
            except Exception:
                return None

        if self._rugcheck is None:
            return None

        try:
            data = await self._rugcheck.get_token_report(mint)
            if data:
                self._cache.set(cache_key, data, ttl=1800.0)  # 30 min
            return data
        except Exception as exc:
            logger.debug("RugCheck fetch failed for %s: %s", mint[:12], exc)
            return None

    # ── Market data fetch ──────────────────────────────────────────────────

    async def _fetch_token_data(self, mint: str) -> Optional[TokenData]:
        """
        Fetch the most current market data for a token.

        Tries DexScreener first; falls back to Pump.fun.
        """
        try:
            dex: DexScreenerProvider = self._providers.get("dexscreener")  # type: ignore[assignment]
            data = await dex.get_token_data(mint)
            if data is not None:
                return data
        except Exception as exc:
            logger.debug("DexScreener fetch failed for %s: %s", mint[:12], exc)

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
        """Assign a scan priority tier based on current token market metrics."""
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

        Sprint 2: VALIDATED → WATCHING → ACCUMULATING → HIGH_PRIORITY
        Sprint 3: HIGH_PRIORITY → READY_FOR_ALERT (scoring engine decides)
                  READY_FOR_ALERT → TRACKING (alert engine decides)

        Returns
        -------
        TokenState | None
            Target state if a transition is warranted; None if no change.
        """
        cfg = self._settings
        vol = token.volume_24h_usd or 0.0
        liq = token.liquidity_usd or 0.0
        current = entry.state

        # Do not re-evaluate already-completed states
        if current in (TokenState.TRACKING, TokenState.ARCHIVED):
            return None

        # VALIDATED → WATCHING
        if current == TokenState.VALIDATED and liq > 0:
            return TokenState.WATCHING

        # WATCHING → ACCUMULATING
        if (
            current == TokenState.WATCHING
            and vol >= cfg.priority_medium_volume
            and liq >= cfg.priority_medium_liquidity
        ):
            return TokenState.ACCUMULATING

        # ACCUMULATING → HIGH_PRIORITY
        if (
            current == TokenState.ACCUMULATING
            and vol >= cfg.priority_high_volume
            and liq >= cfg.priority_high_liquidity
        ):
            return TokenState.HIGH_PRIORITY

        # ACCUMULATING → WATCHING (momentum lost)
        if (
            current == TokenState.ACCUMULATING
            and vol < cfg.priority_medium_volume
            and entry.scan_count > 5
        ):
            return TokenState.WATCHING

        # HIGH_PRIORITY → ACCUMULATING (metrics weakened)
        if (
            current == TokenState.HIGH_PRIORITY
            and vol < cfg.priority_high_volume
        ):
            return TokenState.ACCUMULATING

        return None

    # ── Timing helpers ────────────────────────────────────────────────────

    def _record_scan_time(self, elapsed_seconds: float) -> None:
        self._total_scan_ms += elapsed_seconds * 1000
        self._scan_count_for_avg += 1

    @property
    def avg_scan_time_ms(self) -> Optional[float]:
        if self._scan_count_for_avg == 0:
            return None
        return self._total_scan_ms / self._scan_count_for_avg

    # ── Diagnostics ────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """Return runtime diagnostics for the /diagnostics endpoint."""
        return {
            "cycles": self._cycles,
            "tokens_scanned": self._tokens_scanned,
            "scan_errors": self._scan_errors,
            "avg_scan_time_ms": (
                round(self.avg_scan_time_ms, 1)
                if self.avg_scan_time_ms is not None
                else None
            ),
        }
