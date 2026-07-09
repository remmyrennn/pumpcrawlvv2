"""
Heartbeat module.

Sends a periodic Telegram status message showing runtime health,
watchlist activity, alert counts, provider health, DB status,
and average scan performance.

Sprint 1: architecture only (log stub).
Sprint 2: architecture + scheduler wiring (still log-only).
Sprint 3: full implementation — real Telegram message.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import app as app_module
from app.utils.time_utils import format_uptime, utcnow_iso

if TYPE_CHECKING:
    from app.database.manager import DatabaseManager
    from app.providers.manager import ProviderManager
    from app.scanner.token_scanner import TokenScanner
    from app.scanner.watchlist import WatchlistManager
    from app.telegram.bot import TelegramBot

logger = logging.getLogger(__name__)

HEARTBEAT_JOB_NAME = "heartbeat"


class Heartbeat:
    """
    Periodic system status heartbeat.

    Sends a Telegram message on each tick with a full runtime snapshot:
    uptime, watching count, alerts dispatched today, best performer,
    provider health, DB connectivity, and average scan duration.

    Parameters
    ----------
    interval_seconds:
        Tick interval in seconds.
    enabled:
        When False the heartbeat job is registered but silently skipped.
    """

    def __init__(
        self,
        interval_seconds: int,
        enabled: bool = True,
    ) -> None:
        self._interval = interval_seconds
        self._enabled = enabled
        self._tick_count: int = 0

        # Injected by set_runtime_context() after construction.
        self._bot: Optional["TelegramBot"] = None
        self._db: Optional["DatabaseManager"] = None
        self._watchlist: Optional["WatchlistManager"] = None
        self._scanner: Optional["TokenScanner"] = None
        self._provider_manager: Optional["ProviderManager"] = None
        self._start_time: float = time.time()

    # ── Dependency injection ──────────────────────────────────────────────

    def set_runtime_context(
        self,
        *,
        telegram_bot: Optional["TelegramBot"] = None,
        db: Optional["DatabaseManager"] = None,
        watchlist: Optional["WatchlistManager"] = None,
        scanner: Optional["TokenScanner"] = None,
        provider_manager: Optional["ProviderManager"] = None,
        start_time: Optional[float] = None,
    ) -> None:
        """
        Inject runtime singletons after construction.

        Must be called before the scheduler starts so that the first tick
        has access to live data.
        """
        self._bot = telegram_bot
        self._db = db
        self._watchlist = watchlist
        self._scanner = scanner
        self._provider_manager = provider_manager
        if start_time is not None:
            self._start_time = start_time

    # ── Tick ─────────────────────────────────────────────────────────────

    async def tick(self) -> None:
        """Execute one heartbeat tick and send a Telegram status message."""
        self._tick_count += 1

        if not self._enabled:
            logger.debug("Heartbeat tick #%d skipped (disabled).", self._tick_count)
            return

        logger.info("Heartbeat tick #%d at %s.", self._tick_count, utcnow_iso())

        try:
            message = await self._build_status_message()
        except Exception as exc:
            logger.error("Heartbeat message build failed: %s", exc)
            message = (
                f"💓 <b>MemeCrawler Heartbeat #{self._tick_count}</b>\n"
                f"⚠️ Status build error: {exc}"
            )

        if self._bot is not None and self._bot.is_running:
            try:
                await self._bot.broadcast_message(message)
            except Exception as exc:
                logger.error("Heartbeat Telegram send failed: %s", exc)
        else:
            logger.info("Heartbeat (no bot): %s", message[:200])

    # ── Status message ────────────────────────────────────────────────────

    async def build_message(self) -> str:
        """Public wrapper — lets handlers build the heartbeat text without sending it."""
        return await self._build_status_message()

    async def _build_status_message(self) -> str:
        """Build the full Telegram heartbeat status message."""
        uptime_sec = time.time() - self._start_time
        uptime_str = format_uptime(uptime_sec)

        watching = await self._count_watching()
        alerts_today = await self._count_alerts_today()
        best = await self._best_performer()
        provider_summary = self._provider_health()
        db_status = "connected" if (self._db and self._db.is_connected) else "disconnected"
        avg_scan = self._avg_scan_ms()
        scanner_cycles = self._scanner_cycles()

        return (
            f"💓 <b>MemeCrawler Heartbeat</b>\n\n"
            f"🕐 <b>Uptime:</b>        {uptime_str}\n"
            f"👁 <b>Watching:</b>      {watching} tokens\n"
            f"🚨 <b>Alerts today:</b>  {alerts_today}\n"
            f"🏆 <b>Top token:</b>     {best}\n\n"
            f"<b>Infrastructure</b>\n"
            f"• Providers:  {provider_summary}\n"
            f"• Database:   {db_status}\n"
            f"• Avg scan:   {avg_scan}\n"
            f"• Cycles run: {scanner_cycles}\n\n"
            f"<i>Sprint {app_module.__sprint__} · {utcnow_iso()[:19]} UTC</i>"
        )

    async def _count_watching(self) -> int:
        if self._watchlist is None:
            return 0
        try:
            return await self._watchlist.count_active()
        except Exception:
            return 0

    async def _count_alerts_today(self) -> int:
        if self._db is None:
            return 0
        try:
            row = await self._db.fetchone(
                """
                SELECT COUNT(*) AS cnt FROM alerts
                WHERE alert_type = 'conviction_alert'
                  AND date(sent_at) = date('now')
                """
            )
            return row["cnt"] if row else 0
        except Exception:
            return 0

    async def _best_performer(self) -> str:
        if self._db is None:
            return "—"
        try:
            row = await self._db.fetchone(
                """
                SELECT r.mint, r.score, r.confidence, w.symbol
                FROM rankings r
                LEFT JOIN watchlist w ON w.mint = r.mint
                ORDER BY r.score DESC
                LIMIT 1
                """
            )
            if not row:
                return "—"
            sym = row["symbol"] or row["mint"][:8]
            return f"{sym} (score {row['score']:.0f}, conf {row['confidence']:.0f}%)"
        except Exception:
            return "—"

    def _provider_health(self) -> str:
        if self._provider_manager is None:
            return "—"
        providers = self._provider_manager.all()
        if not providers:
            return "none registered"
        healthy = sum(1 for p in providers if p.is_healthy)
        return f"{healthy}/{len(providers)} healthy"

    def _avg_scan_ms(self) -> str:
        if self._scanner is None:
            return "—"
        info = self._scanner.info()
        avg = info.get("avg_scan_time_ms")
        if avg is None:
            return "—"
        return f"{avg:.0f} ms"

    def _scanner_cycles(self) -> object:
        if self._scanner is None:
            return "—"
        return self._scanner.info().get("cycles", "—")

    # ── Scheduler registration ────────────────────────────────────────────

    def register_with_scheduler(self, scheduler: object) -> None:
        """Register the heartbeat tick with the scheduler."""
        scheduler.register(  # type: ignore[attr-defined]
            name=HEARTBEAT_JOB_NAME,
            func=self.tick,
            interval_seconds=self._interval,
            run_immediately=False,
            enabled=self._enabled,
        )
        logger.info(
            "Heartbeat registered (interval=%ds, enabled=%s).",
            self._interval,
            self._enabled,
        )

    # ── Status ────────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """Return a status summary for the /health API endpoint."""
        return {
            "enabled": self._enabled,
            "interval_seconds": self._interval,
            "tick_count": self._tick_count,
        }
