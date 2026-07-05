"""
Telegram bot framework.

Manages the ``python-telegram-bot`` Application lifecycle and exposes a
clean interface for sending messages from any module in the codebase.

Sprint 1 commands: /start, /help, /ping, /version, /stats
Sprint 2 commands: /watch, /diagnostics  (+ live /stats data)
Sprint 3 commands: /watchlist, /token, /leaderboard, /heartbeat,
                   /marketmode, /editfilters
Sprint 4 commands: /health, /providers, /runtime, /database, /cache
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from telegram.ext import Application, ApplicationBuilder

from app.telegram import handlers
from app.utils.errors import TelegramNotConfiguredError

if TYPE_CHECKING:
    from app.analysis.market_mode import MarketModeDetector
    from app.analysis.ranking import RankingEngine
    from app.cache.manager import CacheManager
    from app.database.manager import DatabaseManager
    from app.heartbeat.heartbeat import Heartbeat
    from app.providers.manager import ProviderManager
    from app.scanner.token_scanner import TokenScanner
    from app.scanner.watchlist import WatchlistManager

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Wrapper around the python-telegram-bot Application.

    Responsibilities
    ----------------
    - Build and start the bot Application.
    - Register all command handlers (delegated to :mod:`app.telegram.handlers`).
    - Provide :meth:`send_message` for outbound messages.
    - Manage graceful startup and shutdown.

    Parameters
    ----------
    token:
        Telegram Bot API token from @BotFather.
    authorized_user_ids:
        List of Telegram user IDs permitted to interact with the bot.
    target_chat:
        Default chat ID for outbound alert messages.
    """

    def __init__(
        self,
        token: str,
        authorized_user_ids: list[int],
        target_chat: str,
    ) -> None:
        if not token.strip():
            raise TelegramNotConfiguredError(
                "BOT_TOKEN is required but was not provided.",
                code="MISSING_BOT_TOKEN",
            )
        self._token = token
        self._authorized_user_ids = authorized_user_ids
        self._target_chat = target_chat
        self._app: Optional[Application] = None

        # Injected by set_runtime_context() before start()
        self._provider_manager: Optional["ProviderManager"] = None
        self._watchlist: Optional["WatchlistManager"] = None
        self._scanner: Optional["TokenScanner"] = None
        self._db: Optional["DatabaseManager"] = None
        self._ranking_engine: Optional["RankingEngine"] = None
        self._market_mode_detector: Optional["MarketModeDetector"] = None
        self._heartbeat: Optional["Heartbeat"] = None
        # Sprint 4
        self._cache: Optional["CacheManager"] = None
        self._start_time: Optional[float] = None

    # ── Dependency injection ──────────────────────────────────────────────

    def set_runtime_context(
        self,
        *,
        provider_manager: Optional["ProviderManager"] = None,
        watchlist: Optional["WatchlistManager"] = None,
        scanner: Optional["TokenScanner"] = None,
        db: Optional["DatabaseManager"] = None,
        ranking_engine: Optional["RankingEngine"] = None,
        market_mode_detector: Optional["MarketModeDetector"] = None,
        heartbeat: Optional["Heartbeat"] = None,
        cache: Optional["CacheManager"] = None,
        start_time: Optional[float] = None,
    ) -> None:
        """
        Inject runtime singletons after construction.

        Must be called before :meth:`start` for handlers to have live data.
        """
        self._provider_manager = provider_manager
        self._watchlist = watchlist
        self._scanner = scanner
        self._db = db
        self._ranking_engine = ranking_engine
        self._market_mode_detector = market_mode_detector
        self._heartbeat = heartbeat
        self._cache = cache
        self._start_time = start_time

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Build the Application, register handlers, and start polling.

        Raises
        ------
        TelegramNotConfiguredError
            When the bot token is missing.
        """
        logger.info("Initialising Telegram bot.")

        self._app = (
            ApplicationBuilder()
            .token(self._token)
            .build()
        )

        handlers.register(
            application=self._app,
            authorized_user_ids=self._authorized_user_ids,
            provider_manager=self._provider_manager,
            watchlist=self._watchlist,
            scanner=self._scanner,
            db=self._db,
            ranking_engine=self._ranking_engine,
            market_mode_detector=self._market_mode_detector,
            heartbeat=self._heartbeat,
            cache=self._cache,
            start_time=self._start_time,
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message"],
        )

        logger.info("Telegram bot started (polling).")

    async def stop(self) -> None:
        """Stop polling and shut down the Application gracefully."""
        if self._app is None:
            return

        logger.info("Stopping Telegram bot.")
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        self._app = None
        logger.info("Telegram bot stopped.")

    # ── Messaging ────────────────────────────────────────────────────────────

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> None:
        """
        Send a text message to the target chat.

        Parameters
        ----------
        text:
            The message body. Supports HTML formatting by default.
        chat_id:
            Target chat ID. Falls back to ``self._target_chat`` when omitted.
        parse_mode:
            Telegram parse mode: "HTML" or "MarkdownV2".
        disable_web_page_preview:
            Suppress link previews (recommended for alert messages).
        """
        if self._app is None:
            raise RuntimeError(
                "TelegramBot has not been started. Call start() first."
            )
        destination = chat_id or self._target_chat
        if not destination:
            logger.warning("send_message called but no target_chat configured.")
            return

        await self._app.bot.send_message(
            chat_id=destination,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
        logger.debug("Message sent to chat %s.", destination)

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True when the bot Application is active."""
        return self._app is not None

    def info(self) -> dict[str, object]:
        """Return a health summary for the /health API endpoint."""
        return {
            "running": self.is_running,
            "target_chat": self._target_chat,
            "authorized_users": len(self._authorized_user_ids),
        }
