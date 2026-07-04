"""
MemeCrawler FastAPI application.

Startup sequence (enforced in :func:`lifespan`)
------------------------------------------------
 1. Initialise logger
 2. Load configuration
 3. Connect SQLite
 4. Create HTTP client
 5. Initialise Cache
 6. Initialise Provider Manager
 7. Initialise Watchlist Manager
 8. Initialise Discovery Engine
 9. Initialise Token Scanner
10. Initialise Telegram bot (when BOT_TOKEN is set)
11. Initialise Sprint 3 Intelligence Layer
    11a. ScoringEngine
    11b. MarketModeDetector
    11c. RankingEngine
    11d. AlertEngine
    11e. MilestoneTracker
    11f. Inject into TokenScanner
12. Initialise Heartbeat (real status messages in Sprint 3)
13. Initialise Scheduler + register jobs
14. Start FastAPI (uvicorn does this externally)

Shutdown sequence
-----------------
1. Stop Scheduler
2. Stop Telegram bot
3. Close HTTP client
4. Close SQLite
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import app as app_module
from app.analysis.alert_engine import AlertEngine
from app.analysis.market_mode import MarketModeDetector
from app.analysis.milestone import MilestoneTracker
from app.analysis.ranking import RankingEngine
from app.analysis.scorer import ScoringEngine
from app.cache.manager import CacheManager
from app.config.settings import get_settings
from app.database.manager import DatabaseManager
from app.discovery.engine import DiscoveryEngine
from app.heartbeat.heartbeat import Heartbeat
from app.http_client import close_http_client, create_http_client
from app.logger import setup_logging
from app.providers.dexscreener import DexScreenerProvider
from app.providers.helius import HeliusProvider
from app.providers.manager import ProviderManager
from app.providers.pumpfun import PumpFunProvider
from app.providers.rpc import SolanaRpcProvider
from app.providers.rugcheck import RugCheckProvider
from app.scanner.scheduler import Scheduler
from app.scanner.token_scanner import TokenScanner
from app.scanner.watchlist import WatchlistManager
from app.telegram.bot import TelegramBot
from app.utils.errors import TelegramNotConfiguredError
from app.utils.time_utils import utcnow_iso

# ── Module-level singletons (populated by lifespan) ──────────────────────────

_start_time: float = time.time()
_db: DatabaseManager | None = None
_provider_manager: ProviderManager | None = None
_telegram_bot: TelegramBot | None = None
_scheduler: Scheduler | None = None
_cache: CacheManager | None = None
_watchlist: WatchlistManager | None = None
_scanner: TokenScanner | None = None
_scorer: ScoringEngine | None = None
_ranking: RankingEngine | None = None
_alert_engine: AlertEngine | None = None
_milestone: MilestoneTracker | None = None
_market_mode: MarketModeDetector | None = None

logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan handler.

    Manages the full startup and shutdown sequence in the correct order.
    All module-level singletons are initialised here and stored on
    ``application.state`` for access in route handlers.
    """
    global _db, _provider_manager, _telegram_bot, _scheduler, _cache
    global _watchlist, _scanner, _scorer, _ranking, _alert_engine
    global _milestone, _market_mode

    # 1. Logger
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "MemeCrawler v%s starting (Sprint %d).",
        app_module.__version__,
        app_module.__sprint__,
    )

    try:
        # 3. Database
        _db = DatabaseManager(settings.sqlite_path)
        await _db.connect()
        application.state.db = _db

        # 4. HTTP client
        http_client = await create_http_client()

        # 5. Cache
        _cache = CacheManager()
        application.state.cache = _cache

        # 6. Provider Manager
        _provider_manager = ProviderManager()

        _dexscreener: DexScreenerProvider | None = None
        _pumpfun: PumpFunProvider | None = None
        _rugcheck_provider: RugCheckProvider | None = None

        if settings.enable_dexscreener:
            _dexscreener = DexScreenerProvider(http_client)
            _provider_manager.register(_dexscreener)
        if settings.enable_pumpfun:
            _pumpfun = PumpFunProvider(http_client)
            _provider_manager.register(_pumpfun)
        if settings.enable_helius and settings.helius_configured:
            _provider_manager.register(
                HeliusProvider(http_client, settings.helius_api_key)
            )
        elif settings.enable_helius:
            logger.warning(
                "ENABLE_HELIUS is True but HELIUS_API_KEY is not set — skipping Helius."
            )
        if settings.enable_rugcheck:
            _rugcheck_provider = RugCheckProvider(http_client)
            _provider_manager.register(_rugcheck_provider)
        _provider_manager.register(SolanaRpcProvider(http_client))

        await _provider_manager.check_all()
        application.state.provider_manager = _provider_manager

        # 7. Watchlist Manager
        _watchlist = WatchlistManager(db=_db, cache=_cache)
        application.state.watchlist = _watchlist

        # 8. Discovery Engine
        _discovery = DiscoveryEngine(
            dexscreener=_dexscreener,
            pumpfun=_pumpfun,
            min_liquidity_usd=settings.min_liquidity_usd,
            blacklisted_tokens=settings.blacklisted_token_set,
            blacklisted_developers=settings.blacklisted_developer_set,
            discovery_limit=settings.discovery_limit,
        )
        application.state.discovery = _discovery

        # 9. Token Scanner
        _scanner = TokenScanner(
            provider_manager=_provider_manager,
            watchlist=_watchlist,
            discovery=_discovery,
            cache=_cache,
            settings=settings,
        )
        application.state.scanner = _scanner

        # 10. Telegram (constructed before intelligence — alert engine needs it)
        _telegram_bot = None
        if settings.bot_configured:
            try:
                _telegram_bot = TelegramBot(
                    token=settings.bot_token,
                    authorized_user_ids=settings.authorized_user_ids,
                    target_chat=settings.target_chat,
                )
            except (TelegramNotConfiguredError, Exception) as exc:
                logger.warning("TelegramBot construction failed: %s", exc)
                _telegram_bot = None
        else:
            logger.warning(
                "BOT_TOKEN is not set — Telegram bot disabled. "
                "Set BOT_TOKEN in .env to enable it."
            )

        # 11. Sprint 3: Intelligence Layer ─────────────────────────────────────

        # 11a. Scoring Engine (stateless — instantiate with settings)
        _scorer = ScoringEngine(settings=settings)
        application.state.scorer = _scorer

        # 11b. Market Mode Detector
        _market_mode = MarketModeDetector(db=_db, settings=settings)
        application.state.market_mode = _market_mode

        # 11c. Ranking Engine
        _ranking = RankingEngine(db=_db)
        application.state.ranking = _ranking

        # 11d. Alert Engine (needs DB, watchlist, optional Telegram)
        _alert_engine = AlertEngine(
            db=_db,
            watchlist=_watchlist,
            telegram_bot=_telegram_bot,
        )
        application.state.alert_engine = _alert_engine

        # 11e. Milestone Tracker (needs DB, optional Telegram)
        _milestone = MilestoneTracker(db=_db, telegram_bot=_telegram_bot)
        application.state.milestone = _milestone

        # 11f. Inject intelligence context into TokenScanner
        _scanner.set_intelligence_context(
            scorer=_scorer,
            alert_engine=_alert_engine,
            ranking=_ranking,
            milestone=_milestone,
            market_mode=_market_mode,
            db=_db,
            rugcheck=_rugcheck_provider,
        )
        logger.info("Sprint 3 intelligence layer initialised.")

        # 12. Heartbeat (full implementation in Sprint 3)
        heartbeat = Heartbeat(
            interval_seconds=settings.heartbeat_interval,
            enabled=settings.enable_heartbeat,
        )
        heartbeat.set_runtime_context(
            telegram_bot=_telegram_bot,
            db=_db,
            watchlist=_watchlist,
            scanner=_scanner,
            provider_manager=_provider_manager,
            start_time=_start_time,
        )

        # 10 (continued): Complete Telegram setup with all Sprint 3 singletons
        if _telegram_bot is not None:
            try:
                _telegram_bot.set_runtime_context(
                    provider_manager=_provider_manager,
                    watchlist=_watchlist,
                    scanner=_scanner,
                    db=_db,
                    ranking_engine=_ranking,
                    market_mode_detector=_market_mode,
                    heartbeat=heartbeat,
                )
                await _telegram_bot.start()
            except Exception as exc:
                logger.warning("Telegram bot start failed: %s", exc)
                _telegram_bot = None
        application.state.telegram_bot = _telegram_bot

        # 13. Scheduler
        _scheduler = Scheduler()
        heartbeat.register_with_scheduler(_scheduler)

        if settings.enable_scanner:
            _scheduler.register(
                name="token_scan",
                func=_scanner.run_cycle,
                interval_seconds=settings.scan_interval,
                run_immediately=True,
                enabled=True,
            )
            logger.info(
                "Token scanner registered (interval=%ds).", settings.scan_interval
            )

        await _scheduler.start()
        application.state.scheduler = _scheduler

        logger.info("MemeCrawler fully started. API is ready.")

    except Exception as exc:
        logger.critical(
            "Startup failed — initiating emergency cleanup: %s", exc
        )
        for label, coro in [
            ("scheduler",   _scheduler.stop() if _scheduler else None),
            ("telegram",    _telegram_bot.stop() if _telegram_bot else None),
            ("http_client", close_http_client()),
            ("database",    _db.close() if _db else None),
        ]:
            if coro is None:
                continue
            try:
                await coro
            except Exception as cleanup_exc:
                logger.error(
                    "Emergency cleanup error for %s: %s", label, cleanup_exc
                )
        raise

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("MemeCrawler shutting down.")
    shutdown_errors: list[str] = []

    for label, coro in [
        ("scheduler",   _scheduler.stop() if _scheduler else None),
        ("telegram",    _telegram_bot.stop() if _telegram_bot else None),
        ("http_client", close_http_client()),
        ("database",    _db.close() if _db else None),
    ]:
        if coro is None:
            continue
        try:
            await coro
        except Exception as exc:
            err = f"{label}: {exc}"
            shutdown_errors.append(err)
            logger.error("Error during shutdown of %s: %s", label, exc)

    if shutdown_errors:
        logger.critical(
            "Shutdown completed with %d error(s): %s",
            len(shutdown_errors),
            "; ".join(shutdown_errors),
        )
    else:
        logger.info("MemeCrawler shutdown complete.")


# ── Application factory ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="MemeCrawler",
        description="Solana memecoin research engine API.",
        version=app_module.__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @application.get("/", summary="Root liveness check")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "status": "online",
                "service": "MemeCrawler",
                "version": app_module.__version__,
                "sprint": app_module.__sprint__,
            }
        )

    @application.get("/health", summary="Detailed health status")
    async def health() -> JSONResponse:
        """Return the full health status of all subsystems."""
        db_health = await _db.health_check() if _db else {"connected": False}
        provider_info = _provider_manager.info() if _provider_manager else {}
        telegram_info = _telegram_bot.info() if _telegram_bot else {"running": False}
        scheduler_info = _scheduler.info() if _scheduler else {"running": False}
        cache_info = _cache.info() if _cache else {}
        scanner_info = _scanner.info() if _scanner else {}
        market_info = _market_mode.info() if _market_mode else {}
        ranking_info = _ranking.info() if _ranking else {}

        overall_status = "healthy"
        if not db_health.get("connected"):
            overall_status = "degraded"

        return JSONResponse(
            content={
                "status": overall_status,
                "uptime_seconds": round(time.time() - _start_time, 1),
                "version": app_module.__version__,
                "sprint": app_module.__sprint__,
                "timestamp": utcnow_iso(),
                "database": db_health,
                "telegram": telegram_info,
                "providers": provider_info,
                "scheduler": scheduler_info,
                "cache": cache_info,
                "scanner": scanner_info,
                "market_mode": market_info,
                "ranking": ranking_info,
            }
        )

    return application


# ── Module-level app instance (used by uvicorn) ────────────────────────────────

app = create_app()
