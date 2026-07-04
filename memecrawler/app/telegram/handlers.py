"""
Telegram command handlers.

Every user-facing command is implemented here as a standalone async
function. The :func:`register` function wires them to the Application so
that ``bot.py`` stays focused on lifecycle management.

Sprint 1 commands: /start, /help, /ping, /version, /stats
Sprint 2 commands: /watch, /diagnostics  (+ /stats updated with real data)

Sprint 2+ will add watchlist and alert management commands.
"""

from __future__ import annotations

import logging
import platform
import sys
from functools import wraps
from typing import TYPE_CHECKING, Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import app as app_module
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.providers.manager import ProviderManager
    from app.scanner.token_scanner import TokenScanner
    from app.scanner.watchlist import WatchlistManager

logger = logging.getLogger(__name__)

# ── Start time (module load = process start approximation) ────────────────────

_MODULE_LOADED_AT: str = utcnow_iso()

# ── Auth decorator ────────────────────────────────────────────────────────────

def _authorised(authorized_user_ids: list[int]) -> Callable:
    """
    Decorator factory that restricts a command handler to authorised users.

    **Secure by default**: when ``authorized_user_ids`` is empty, *all*
    commands are denied (not permitted). Set ``AUTHORIZED_USERS`` in your
    environment to grant access to specific Telegram user IDs.

    Parameters
    ----------
    authorized_user_ids:
        List of permitted Telegram user IDs.  Must be non-empty for any
        command to succeed.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_user:
                return
            user_id = update.effective_user.id
            if not authorized_user_ids or user_id not in authorized_user_ids:
                logger.warning(
                    "Unauthorised access attempt by user %d on command '%s'.",
                    user_id,
                    update.effective_message.text if update.effective_message else "?",
                )
                await update.message.reply_text(
                    "⛔ You are not authorised to use MemeCrawler."
                )
                return
            await func(update, context)
        return wrapper
    return decorator


# ── Command handlers ──────────────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    user = update.effective_user
    first_name = user.first_name if user else "Researcher"
    text = (
        f"👋 <b>Welcome to MemeCrawler, {first_name}!</b>\n\n"
        "MemeCrawler is your personal Solana memecoin research engine.\n\n"
        "It continuously scans Solana tokens, evaluates risk, tracks promising "
        "projects over time, and only alerts you after multiple confirmations.\n\n"
        "<b>Philosophy:</b> Quality over quantity. No sniping. No FOMO.\n\n"
        "Use /help to see available commands."
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command."""
    text = (
        "🔍 <b>MemeCrawler — Command Reference</b>\n\n"
        "<b>Information</b>\n"
        "/start       — Welcome message\n"
        "/help        — This reference\n"
        "/ping        — Check bot liveness\n"
        "/version     — Module and sprint versions\n"
        "/stats       — Live system statistics\n"
        "/diagnostics — Provider health details\n"
        "/watch       — Current watchlist\n\n"
        "<i>Sprint 3 will add: /alerts, /summary, /risk &lt;mint&gt;</i>\n"
        "<i>Sprint 4 will add: /sync, /export, cloud backup commands</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /ping command."""
    await update.message.reply_text("🏓 Pong! MemeCrawler is online.")


async def _cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /version command."""
    py_version = platform.python_version()
    text = (
        "⚙️ <b>MemeCrawler — Version Info</b>\n\n"
        f"<b>Application:</b> {app_module.__version__}\n"
        f"<b>Sprint:</b>      {app_module.__sprint__}\n\n"
        "<b>Runtime</b>\n"
        f"• Python:              {py_version}\n"
        f"• FastAPI:             0.115.x\n"
        f"• python-telegram-bot: 21.x\n"
        f"• httpx:               0.28.x\n"
        f"• aiosqlite:           0.20.x\n"
        f"• pydantic-settings:   2.6.x\n"
        f"• tenacity:            9.x\n\n"
        "<b>Providers</b>\n"
        "• DexScreener  v2.0.0\n"
        "• Pump.fun     v2.0.0\n"
        "• Helius       v1.0.0\n"
        "• RugCheck     v2.0.0\n"
        "• Solana RPC   v2.0.0"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /stats command.

    Displays live system statistics sourced from the application's runtime
    singletons (injected via bot_data at registration time).
    """
    py_info = f"{platform.python_implementation()} {platform.python_version()}"
    os_info = f"{platform.system()} {platform.release()}"

    # Pull runtime data from bot_data (set in register())
    provider_manager: ProviderManager | None = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    watchlist: WatchlistManager | None = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    scanner: TokenScanner | None = (
        context.bot_data.get("scanner") if context.bot_data else None
    )

    # Provider health
    provider_lines = ""
    if provider_manager:
        for p in provider_manager.all():
            status_icon = "✅" if p.is_healthy else "⚠️"
            provider_lines += f"  {status_icon} {p.name}: {p.status.value}\n"
    else:
        provider_lines = "  — not available —\n"

    # Watchlist stats
    watchlist_lines = ""
    if watchlist:
        try:
            counts = await watchlist.count_by_state()
            total = sum(counts.values())
            active = await watchlist.count_active()
            watchlist_lines = (
                f"  Total tokens:  {total}\n"
                f"  Active:        {active}\n"
            )
            for state, cnt in sorted(counts.items()):
                if cnt > 0:
                    watchlist_lines += f"  {state}: {cnt}\n"
        except Exception:
            watchlist_lines = "  — error reading watchlist —\n"
    else:
        watchlist_lines = "  — not available —\n"

    # Scanner stats
    scanner_lines = ""
    if scanner:
        info = scanner.info()
        scanner_lines = (
            f"  Cycles:        {info['cycles']}\n"
            f"  Tokens scanned:{info['tokens_scanned']}\n"
            f"  Scan errors:   {info['scan_errors']}\n"
        )
    else:
        scanner_lines = "  — not available —\n"

    text = (
        "📊 <b>MemeCrawler — Live System Stats</b>\n\n"
        f"<b>Runtime:</b>  {py_info}\n"
        f"<b>Platform:</b> {os_info}\n"
        f"<b>Online since:</b> {_MODULE_LOADED_AT[:19]}\n\n"
        "<b>Providers</b>\n"
        f"{provider_lines}"
        "<b>Watchlist</b>\n"
        f"{watchlist_lines}"
        "<b>Scanner</b>\n"
        f"{scanner_lines}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /watch command.

    Displays the current watchlist grouped by scan priority.
    """
    watchlist: WatchlistManager | None = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )

    if watchlist is None:
        await update.message.reply_text(
            "⚠️ Watchlist is not available.", parse_mode="HTML"
        )
        return

    try:
        entries = await watchlist.get_all(include_archived=False)
    except Exception as exc:
        logger.error("Failed to read watchlist for /watch: %s", exc)
        await update.message.reply_text(
            "❌ Failed to read watchlist.", parse_mode="HTML"
        )
        return

    if not entries:
        await update.message.reply_text(
            "📋 <b>Watchlist is empty.</b>\n\n"
            "The scanner hasn't discovered any tokens yet. "
            "Check back after the first scan cycle.",
            parse_mode="HTML",
        )
        return

    # Group by priority
    from app.models.token import ScanPriority
    groups: dict[str, list] = {p.value: [] for p in ScanPriority}
    for entry in entries:
        groups[entry.priority.value].append(entry)

    lines: list[str] = [f"📋 <b>Watchlist — {len(entries)} token(s)</b>\n"]
    priority_icons = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }

    for priority_name in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        bucket = groups.get(priority_name, [])
        if not bucket:
            continue
        icon = priority_icons.get(priority_name, "⚪")
        lines.append(f"\n{icon} <b>{priority_name}</b> ({len(bucket)})")
        for entry in bucket[:10]:  # cap at 10 per priority tier
            mc = _fmt_usd(entry.market_cap_usd)
            liq = _fmt_usd(entry.liquidity_usd)
            symbol = entry.symbol or entry.mint[:8]
            lines.append(
                f"  • <code>{symbol}</code>  MC:{mc}  Liq:{liq}"
                f"  [{entry.state.value}]  scans:{entry.scan_count}"
            )
        if len(bucket) > 10:
            lines.append(f"  <i>…and {len(bucket) - 10} more</i>")

    # Telegram message limit is 4096 characters
    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"

    await update.message.reply_text(message, parse_mode="HTML")


async def _cmd_diagnostics(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle the /diagnostics command.

    Displays detailed provider health metrics including latency, error
    counts, and last success/failure timestamps.
    """
    provider_manager: ProviderManager | None = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    scanner: TokenScanner | None = (
        context.bot_data.get("scanner") if context.bot_data else None
    )
    watchlist: WatchlistManager | None = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )

    lines: list[str] = ["🔧 <b>MemeCrawler — Diagnostics</b>\n"]

    # ── Providers ──────────────────────────────────────────────────────────
    lines.append("<b>Providers</b>")
    if provider_manager:
        for p in provider_manager.all():
            info = p.info()
            status_icon = "✅" if p.is_healthy else ("⚠️" if info["error_count"] < 3 else "❌")
            latency = (
                f"{info['latency_ms']:.0f}ms" if info["latency_ms"] is not None else "—"
            )
            last_ok = (info["last_success_at"] or "never")[:19]
            last_fail = (info["last_failure_at"] or "never")[:19]
            lines.append(
                f"{status_icon} <b>{info['name']}</b> v{info['version']}\n"
                f"   Status:   {info['status']}  |  Latency: {latency}\n"
                f"   Requests: {info['total_requests']}  |  Errors: {info['error_count']}\n"
                f"   Last OK:  {last_ok}\n"
                f"   Last Fail:{last_fail}"
            )
    else:
        lines.append("  — provider manager not available —")

    # ── Scanner ────────────────────────────────────────────────────────────
    lines.append("\n<b>Scanner</b>")
    if scanner:
        s = scanner.info()
        lines.append(
            f"  Cycles:  {s['cycles']}\n"
            f"  Scanned: {s['tokens_scanned']}\n"
            f"  Errors:  {s['scan_errors']}"
        )
    else:
        lines.append("  — scanner not available —")

    # ── Watchlist ──────────────────────────────────────────────────────────
    lines.append("\n<b>Watchlist</b>")
    if watchlist:
        w = watchlist.info()
        lines.append(
            f"  Added:       {w['tokens_added']}\n"
            f"  Scans logged:{w['scans_recorded']}\n"
            f"  Transitions: {w['state_transitions']}"
        )
    else:
        lines.append("  — watchlist not available —")

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"

    await update.message.reply_text(message, parse_mode="HTML")


# ── Registration ──────────────────────────────────────────────────────────────

def register(
    application: Application,
    authorized_user_ids: list[int],
    *,
    provider_manager: "ProviderManager | None" = None,
    watchlist: "WatchlistManager | None" = None,
    scanner: "TokenScanner | None" = None,
) -> None:
    """
    Register all command handlers on the given Application.

    Runtime singletons (provider_manager, watchlist, scanner) are stored
    in ``bot_data`` so that handlers can access live data without globals.

    Parameters
    ----------
    application:
        The ``python-telegram-bot`` Application instance.
    authorized_user_ids:
        User IDs to restrict access to.
    provider_manager:
        Live provider manager for /stats and /diagnostics.
    watchlist:
        Live watchlist manager for /watch and /stats.
    scanner:
        Live token scanner for /stats and /diagnostics.
    """
    # Store singletons in bot_data for handler access
    application.bot_data["provider_manager"] = provider_manager
    application.bot_data["watchlist"] = watchlist
    application.bot_data["scanner"] = scanner

    auth = _authorised(authorized_user_ids)

    handlers: list[tuple[str, Callable]] = [
        ("start",       auth(_cmd_start)),
        ("help",        auth(_cmd_help)),
        ("ping",        auth(_cmd_ping)),
        ("version",     auth(_cmd_version)),
        ("stats",       auth(_cmd_stats)),
        ("watch",       auth(_cmd_watch)),
        ("diagnostics", auth(_cmd_diagnostics)),
    ]

    for command, handler_func in handlers:
        application.add_handler(CommandHandler(command, handler_func))
        logger.debug("Registered command handler: /%s", command)

    logger.info("All Telegram command handlers registered (%d total).", len(handlers))


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _fmt_usd(value: float | None) -> str:
    """Format a USD value compactly for Telegram messages."""
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"
