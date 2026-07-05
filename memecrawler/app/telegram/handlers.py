"""
Telegram command handlers.

Every user-facing command is implemented here as a standalone async
function. The :func:`register` function wires them to the Application so
that ``bot.py`` stays focused on lifecycle management.

Sprint 1 commands: /start, /help, /ping, /version, /stats
Sprint 2 commands: /watch, /diagnostics  (+ /stats updated with real data)
Sprint 3 commands: /leaderboard, /watchlist, /token, /heartbeat,
                   /marketmode, /editfilters
Sprint 4 commands: /health, /providers, /runtime, /database, /cache
"""

from __future__ import annotations

import logging
import platform
import time
from functools import wraps
from typing import TYPE_CHECKING, Callable, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import app as app_module
from app.utils.time_utils import format_duration, format_uptime, utcnow_iso

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

_MODULE_LOADED_AT: str = utcnow_iso()

# ── Auth decorator ────────────────────────────────────────────────────────────

def _authorised(authorized_user_ids: list[int]) -> Callable:
    """
    Decorator factory that restricts a command handler to authorised users.

    Secure by default: when ``authorized_user_ids`` is empty, *all*
    commands are denied. Set ``AUTHORIZED_USERS`` in .env to grant access.
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


# ── Sprint 1 / 2 handlers ─────────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    text = (
        "🔍 <b>MemeCrawler v4 — Command Reference</b>\n\n"
        "<b>Information</b>\n"
        "/start        — Welcome message\n"
        "/help         — This reference\n"
        "/ping         — Check bot liveness\n"
        "/version      — Module and sprint versions\n"
        "/stats        — Live system statistics\n"
        "/diagnostics  — Provider health details\n\n"
        "<b>Watchlist &amp; Scoring</b>\n"
        "/watch        — Watchlist grouped by priority\n"
        "/watchlist    — Watchlist with score/confidence data\n"
        "/token &lt;mint&gt; — Detailed token evaluation\n"
        "/leaderboard  — Top tokens by conviction/confidence\n\n"
        "<b>Market Intelligence</b>\n"
        "/heartbeat    — Manual status report\n"
        "/marketmode   — Current market mode (BULL/NEUTRAL/WEAK)\n\n"
        "<b>Production Monitoring (Sprint 4)</b>\n"
        "/health       — Full subsystem health summary\n"
        "/providers    — Provider latency and success rates\n"
        "/runtime      — Python version, uptime, memory usage\n"
        "/database     — SQLite health and row counts\n"
        "/cache        — In-process cache statistics\n\n"
        "<b>Configuration</b>\n"
        "/editfilters  — View or adjust runtime filters"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏓 Pong! MemeCrawler is online.")


async def _cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    py_info = f"{platform.python_implementation()} {platform.python_version()}"
    os_info = f"{platform.system()} {platform.release()}"

    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    scanner: "TokenScanner | None" = (
        context.bot_data.get("scanner") if context.bot_data else None
    )

    provider_lines = ""
    if provider_manager:
        for p in provider_manager.all():
            status_icon = "✅" if p.is_healthy else "⚠️"
            provider_lines += f"  {status_icon} {p.name}: {p.status.value}\n"
    else:
        provider_lines = "  — not available —\n"

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

    scanner_lines = ""
    if scanner:
        info = scanner.info()
        avg = info.get("avg_scan_time_ms")
        avg_str = f"{avg:.0f}ms" if avg is not None else "—"
        scanner_lines = (
            f"  Cycles:        {info['cycles']}\n"
            f"  Tokens scanned:{info['tokens_scanned']}\n"
            f"  Scan errors:   {info['scan_errors']}\n"
            f"  Avg scan time: {avg_str}\n"
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
    /watch — Watchlist grouped by priority (Sprint 2).

    Shows tokens ordered by CRITICAL → HIGH → MEDIUM → LOW priority.
    """
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    if not watchlist:
        await update.message.reply_text("⚠️ Watchlist not available.")
        return

    try:
        entries = await watchlist.all_active()
    except Exception as exc:
        logger.error("_cmd_watch: failed to fetch watchlist: %s", exc)
        await update.message.reply_text("❌ Failed to fetch watchlist data.")
        return

    if not entries:
        await update.message.reply_text(
            "📋 <b>Watchlist is empty.</b>\n\n"
            "The scanner will populate it automatically.",
            parse_mode="HTML",
        )
        return

    by_priority: dict[str, list] = {
        "CRITICAL": [],
        "HIGH": [],
        "MEDIUM": [],
        "LOW": [],
    }
    for entry in entries:
        pri = getattr(entry, "priority", "LOW")
        if pri in by_priority:
            by_priority[pri].append(entry)
        else:
            by_priority["LOW"].append(entry)

    lines = [f"📋 <b>Watchlist ({len(entries)} tokens)</b>\n"]
    for priority, bucket in by_priority.items():
        if not bucket:
            continue
        icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}[priority]
        lines.append(f"\n{icon} <b>{priority}</b> ({len(bucket)})")
        for entry in bucket[:10]:
            sym = getattr(entry, "symbol", "") or "?"
            state = getattr(entry, "state", "?")
            lines.append(f"  • {sym} [{state}]")
        if len(bucket) > 10:
            lines.append(f"  <i>…and {len(bucket) - 10} more</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_diagnostics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /diagnostics — Detailed provider health report.
    """
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    if not provider_manager:
        await update.message.reply_text("⚠️ Provider manager not available.")
        return

    lines = ["🔬 <b>Provider Diagnostics</b>\n"]
    for p in provider_manager.all():
        info = p.info()
        status_icon = "✅" if p.is_healthy else ("⚠️" if info["status"] == "degraded" else "❌")
        latency = f"{info['latency_ms']}ms" if info.get("latency_ms") is not None else "—"
        last_ok = str(info.get("last_success_at") or "never")[:19]
        last_fail = str(info.get("last_failure_at") or "never")[:19]
        lines.append(
            f"{status_icon} <b>{info['name']}</b> v{info['version']}\n"
            f"   Status: {info['status']} | Latency: {latency}\n"
            f"   Requests: {info['total_requests']} | Errors: {info['error_count']}\n"
            f"   Last OK:   {last_ok}\n"
            f"   Last Fail: {last_fail}"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


# ── Sprint 3 handlers ─────────────────────────────────────────────────────────

async def _cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /watchlist — Watchlist with score and confidence (Sprint 3).
    """
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    if not watchlist:
        await update.message.reply_text("⚠️ Watchlist not available.")
        return

    try:
        entries = await watchlist.all_active()
    except Exception as exc:
        logger.error("_cmd_watchlist: error: %s", exc)
        await update.message.reply_text("❌ Failed to fetch watchlist data.")
        return

    if not entries:
        await update.message.reply_text(
            "📋 <b>Watchlist is empty.</b>", parse_mode="HTML"
        )
        return

    lines = [f"📋 <b>Watchlist — {len(entries)} token(s)</b>\n"]
    for entry in entries[:20]:
        sym = getattr(entry, "symbol", "?") or "?"
        state = getattr(entry, "state", "?")
        score = getattr(entry, "score", None)
        confidence = getattr(entry, "confidence", None)
        risk = getattr(entry, "risk_level", "?")
        score_str = f"{score:.0f}" if score is not None else "—"
        conf_str = f"{confidence:.0f}%" if confidence is not None else "—"
        lines.append(
            f"• <b>{sym}</b> [{state}] Score:{score_str} Conf:{conf_str} Risk:{risk}"
        )

    if len(entries) > 20:
        lines.append(f"\n<i>…and {len(entries) - 20} more tokens</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /token <mint> — Detailed token evaluation (Sprint 3).
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ Usage: /token &lt;mint_address&gt;", parse_mode="HTML"
        )
        return

    mint = args[0].strip()

    if not db:
        await update.message.reply_text("⚠️ Database not available.")
        return

    try:
        wl_row = await db.fetchone(
            "SELECT * FROM watchlist WHERE mint = ?;", (mint,)
        )
        eval_row = await db.fetchone(
            "SELECT * FROM evaluations WHERE mint = ? ORDER BY evaluated_at DESC LIMIT 1;",
            (mint,),
        )
        rank_row = await db.fetchone(
            "SELECT * FROM rankings WHERE mint = ?;", (mint,)
        )
        outcome_row = await db.fetchone(
            "SELECT * FROM outcomes WHERE mint = ?;", (mint,)
        )
    except Exception as exc:
        logger.error("_cmd_token: DB error for %s: %s", mint, exc)
        await update.message.reply_text("❌ Database error.")
        return

    if not wl_row:
        await update.message.reply_text(
            f"❓ Token <code>{mint[:12]}…</code> not found in watchlist.",
            parse_mode="HTML",
        )
        return

    sym = wl_row["symbol"] or "?"
    name = wl_row["name"] or "?"
    state = wl_row["state"]
    priority = wl_row["priority"]
    scans = wl_row["scan_count"] or 0
    risk = wl_row["risk_level"] or "UNKNOWN"

    score_line = "Score:      —"
    if eval_row:
        score_line = (
            f"Score:      {eval_row['score']:.1f} / {eval_row['max_score']:.0f}\n"
            f"Confidence: {eval_row['confidence']:.1f}%\n"
            f"Market:     {eval_row['market_mode']}"
        )

    rank_line = ""
    if rank_row:
        rank_line = f"\nRank:       #{rank_row['rank']} ({rank_row['rank_type']})"

    outcome_line = ""
    if outcome_row:
        gain = outcome_row["current_gain_pct"]
        peak = outcome_row["peak_gain_pct"]
        outcome_line = (
            f"\n\n📈 <b>Post-Alert Tracking</b>\n"
            f"Current gain: {gain:+.1f}%\n"
            f"Peak gain:    {peak:+.1f}%\n"
            f"Outcome:      {outcome_row['outcome']}"
        )

    text = (
        f"🔎 <b>{sym}</b> — {name}\n\n"
        f"<code>{mint}</code>\n\n"
        f"State:      {state}\n"
        f"Priority:   {priority}\n"
        f"Risk:       {risk}\n"
        f"Scans:      {scans}\n"
        f"{score_line}{rank_line}"
        f"{outcome_line}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_leaderboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /leaderboard [conviction|confidence|improvement] — Top tokens (Sprint 3).
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    ranking_engine: "RankingEngine | None" = (
        context.bot_data.get("ranking_engine") if context.bot_data else None
    )

    if not db:
        await update.message.reply_text("⚠️ Database not available.")
        return

    args = context.args or []
    sort_key = args[0].lower() if args else "conviction"
    valid_sorts = {"conviction", "confidence", "improvement"}
    if sort_key not in valid_sorts:
        await update.message.reply_text(
            f"⚠️ Unknown sort. Valid options: {', '.join(sorted(valid_sorts))}"
        )
        return

    try:
        rows = await db.fetchall(
            "SELECT * FROM rankings ORDER BY score DESC LIMIT 10;"
        )
    except Exception as exc:
        logger.error("_cmd_leaderboard: DB error: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return

    if not rows:
        await update.message.reply_text(
            "📊 <b>Leaderboard is empty.</b>\n\n"
            "Tokens need at least one scoring cycle to appear here.",
            parse_mode="HTML",
        )
        return

    sort_label = {"conviction": "Conviction Score", "confidence": "Confidence", "improvement": "Improvement"}[sort_key]
    lines = [f"🏆 <b>Leaderboard — Top {len(rows)} by {sort_label}</b>\n"]

    for i, row in enumerate(rows, start=1):
        sym = row["symbol"] or "?"
        score = row["score"]
        conf = row["confidence"]
        risk = row["risk_level"]
        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(risk, "⚪")
        lines.append(
            f"#{i} {risk_icon} <b>{sym}</b>  Score:{score:.0f}  Conf:{conf:.0f}%  Risk:{risk}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_heartbeat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /heartbeat — Trigger a manual heartbeat status report (Sprint 3).
    """
    heartbeat: "Heartbeat | None" = (
        context.bot_data.get("heartbeat") if context.bot_data else None
    )
    if not heartbeat:
        await update.message.reply_text("⚠️ Heartbeat not available.")
        return

    try:
        await heartbeat.tick()
    except Exception as exc:
        logger.error("_cmd_heartbeat: error: %s", exc)
        await update.message.reply_text("❌ Failed to send heartbeat.")


async def _cmd_marketmode(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /marketmode — Show the current market mode (Sprint 3).
    """
    market_mode_detector: "MarketModeDetector | None" = (
        context.bot_data.get("market_mode_detector") if context.bot_data else None
    )
    if not market_mode_detector:
        await update.message.reply_text("⚠️ Market mode detector not available.")
        return

    info = market_mode_detector.info()
    mode = info.get("mode", "NEUTRAL")
    sample = info.get("sample_size", 0)
    updated = str(info.get("last_updated", "unknown"))[:19]

    mode_icon = {"BULL": "🐂", "NEUTRAL": "➡️", "WEAK": "🐻"}
    icon = mode_icon.get(mode, "➡️")

    mode_desc = {
        "BULL": "Strong positive momentum across watched tokens.",
        "NEUTRAL": "Mixed signals — no clear directional bias.",
        "WEAK": "Majority of tokens showing negative or flat trend.",
    }

    text = (
        f"📈 <b>Market Mode: {icon} {mode}</b>\n\n"
        f"{mode_desc.get(mode, '')}\n\n"
        f"<b>Sample size:</b> {sample} tokens\n"
        f"<b>Last updated:</b> {updated} UTC\n\n"
        "<i>Refreshed each scanner cycle.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_editfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /editfilters [key] [value] — View or update runtime filter overrides.

    Supported keys:
      min_liquidity_usd   — minimum discovery liquidity filter
      scan_interval       — scan cycle interval in seconds
      min_alert_score     — minimum score to dispatch an alert
      min_alert_confidence— minimum confidence to dispatch an alert
      min_alert_scans     — minimum scans before alerting

    Usage:
      /editfilters                  — show current values
      /editfilters min_liquidity_usd 1000
    """
    from app.config.settings import (
        get_settings,
        get_runtime_override,
        set_runtime_override,
        clear_runtime_override,
    )

    EDITABLE_KEYS: dict[str, type] = {
        "min_liquidity_usd":    float,
        "min_alert_score":      float,
        "min_alert_confidence": float,
        "min_alert_scans":      int,
    }

    args = context.args or []
    settings = get_settings()

    if not args:
        # Show current values
        lines = ["⚙️ <b>Runtime Filters</b>\n"]
        for key, _ in EDITABLE_KEYS.items():
            raw = get_runtime_override(key)
            base = getattr(settings, key, "—")
            if raw is not None:
                lines.append(f"  <b>{key}</b>: {raw} <i>(overridden, base: {base})</i>")
            else:
                lines.append(f"  <b>{key}</b>: {base} <i>(default)</i>")
        lines.append(
            "\n<i>Usage: /editfilters &lt;key&gt; &lt;value&gt;\n"
            "Use /editfilters &lt;key&gt; reset to restore default.</i>"
        )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: /editfilters &lt;key&gt; &lt;value&gt;", parse_mode="HTML"
        )
        return

    key = args[0].lower()
    raw_value = args[1].strip()

    if key not in EDITABLE_KEYS:
        valid = ", ".join(EDITABLE_KEYS.keys())
        await update.message.reply_text(
            f"❌ Unknown filter key: <code>{key}</code>\nValid keys: {valid}",
            parse_mode="HTML",
        )
        return

    if raw_value.lower() == "reset":
        clear_runtime_override(key)
        base = getattr(settings, key, "—")
        await update.message.reply_text(
            f"✅ <b>{key}</b> reset to default: <code>{base}</code>",
            parse_mode="HTML",
        )
        return

    try:
        typed_value = EDITABLE_KEYS[key](raw_value)
    except (ValueError, TypeError):
        await update.message.reply_text(
            f"❌ Invalid value for <b>{key}</b>: <code>{raw_value}</code>",
            parse_mode="HTML",
        )
        return

    set_runtime_override(key, typed_value)
    logger.info(
        "Runtime filter override: %s = %s (by user %s)",
        key, typed_value,
        update.effective_user.id if update.effective_user else "?",
    )
    await update.message.reply_text(
        f"✅ <b>{key}</b> set to <code>{typed_value}</code>",
        parse_mode="HTML",
    )


# ── Sprint 4 handlers ─────────────────────────────────────────────────────────

async def _cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /health — Full subsystem health summary (Sprint 4).
    """
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    start_time: Optional[float] = (
        context.bot_data.get("start_time") if context.bot_data else None
    )

    # DB status
    db_ok = db.is_connected if db else False

    # Provider summary
    healthy_providers = 0
    total_providers = 0
    if provider_manager:
        providers = provider_manager.all()
        total_providers = len(providers)
        healthy_providers = sum(1 for p in providers if p.is_healthy)

    # Token counts
    watched = 0
    tracked = 0
    alerts_today = 0
    if db and db_ok:
        try:
            r = await db.fetchone(
                "SELECT COUNT(*) AS cnt FROM watchlist "
                "WHERE state NOT IN ('ARCHIVED', 'TRACKING');"
            )
            watched = r["cnt"] if r else 0
            r2 = await db.fetchone(
                "SELECT COUNT(*) AS cnt FROM watchlist WHERE state = 'TRACKING';"
            )
            tracked = r2["cnt"] if r2 else 0
            r3 = await db.fetchone(
                "SELECT COUNT(*) AS cnt FROM alerts WHERE date(sent_at) = date('now');"
            )
            alerts_today = r3["cnt"] if r3 else 0
        except Exception:
            pass

    # Uptime
    uptime_str = format_uptime(start_time) if start_time else "unknown"

    # Overall status
    if not db_ok:
        overall = "🔴 DEGRADED"
    elif healthy_providers < total_providers:
        overall = "🟡 PARTIAL"
    else:
        overall = "🟢 HEALTHY"

    text = (
        f"🏥 <b>MemeCrawler v{app_module.__version__} — Health</b>\n\n"
        f"Status:     {overall}\n"
        f"Uptime:     {uptime_str}\n\n"
        f"💾 Database:  {'connected' if db_ok else '❌ disconnected'}\n"
        f"📡 Providers: {healthy_providers}/{total_providers} healthy\n"
        f"👁 Watching:  {watched} tokens\n"
        f"🎯 Tracking:  {tracked} tokens\n"
        f"🚨 Alerts today: {alerts_today}\n\n"
        f"<i>Use /providers, /database, /cache, /runtime for details.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_providers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /providers — Provider latency, success rates, and status (Sprint 4).
    """
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    if not provider_manager:
        await update.message.reply_text("⚠️ Provider manager not available.")
        return

    providers = provider_manager.all()
    if not providers:
        await update.message.reply_text("⚠️ No providers registered.")
        return

    lines = [f"📡 <b>Providers ({len(providers)} registered)</b>\n"]
    for p in providers:
        info = p.info()
        status_icon = (
            "✅" if info["status"] == "healthy"
            else ("⚠️" if info["status"] == "degraded" else "❌")
        )
        latency = f"{info['latency_ms']}ms" if info.get("latency_ms") is not None else "—"
        rate = f"{info['success_rate']}%" if info.get("success_rate") is not None else "n/a"
        last_ok = str(info.get("last_success_at") or "never")[:19]
        last_fail = str(info.get("last_failure_at") or "never")[:19]
        lines.append(
            f"{status_icon} <b>{info['name']}</b>\n"
            f"  Status:   {info['status']} | Latency: {latency}\n"
            f"  Requests: {info['total_requests']} ok:{info['total_successes']} rate:{rate}\n"
            f"  Last OK:   {last_ok}\n"
            f"  Last Fail: {last_fail}"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def _cmd_runtime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /runtime — Python version, platform, uptime, and memory usage (Sprint 4).
    """
    start_time: Optional[float] = (
        context.bot_data.get("start_time") if context.bot_data else None
    )

    py_version = platform.python_version()
    py_impl = platform.python_implementation()
    sys_platform = f"{platform.system()} {platform.machine()}"
    uptime_str = format_uptime(start_time) if start_time else "unknown"

    # Memory usage (optional — psutil may not be installed)
    memory_line = ""
    try:
        import psutil
        proc = psutil.Process()
        mem_mb = proc.memory_info().rss / 1_048_576
        memory_line = f"Memory:     {mem_mb:.1f} MB\n"
    except Exception:
        memory_line = "Memory:     unavailable\n"

    text = (
        f"⚙️ <b>MemeCrawler — Runtime</b>\n\n"
        f"Version:    {app_module.__version__} (Sprint {app_module.__sprint__})\n"
        f"Python:     {py_impl} {py_version}\n"
        f"Platform:   {sys_platform}\n"
        f"Uptime:     {uptime_str}\n"
        f"{memory_line}"
        f"Timestamp:  {utcnow_iso()[:19]} UTC"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /database — SQLite health, table sizes, and maintenance status (Sprint 4).
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    if not db:
        await update.message.reply_text("⚠️ Database not available.")
        return

    if not db.is_connected:
        await update.message.reply_text("❌ Database is not connected.")
        return

    try:
        counts = await db.table_row_counts()
        size_bytes = await db.db_file_size_bytes()
    except Exception as exc:
        logger.error("_cmd_database: error: %s", exc)
        await update.message.reply_text("❌ Failed to fetch database info.")
        return

    size_str = (
        f"{size_bytes / 1_048_576:.2f} MB"
        if size_bytes >= 1_048_576
        else f"{size_bytes / 1024:.1f} KB"
        if size_bytes >= 1024
        else f"{size_bytes} B"
    )

    # Format table counts
    count_lines = ""
    for table, cnt in sorted(counts.items()):
        count_lines += f"  {table}: {cnt:,}\n"

    text = (
        f"💾 <b>Database</b>\n\n"
        f"Status:   connected\n"
        f"Path:     {db.path}\n"
        f"Size:     {size_str}\n"
        f"Tables:   {len(counts)}\n\n"
        f"<b>Row counts:</b>\n"
        f"{count_lines}"
        f"\n<i>Use /health for maintenance and cleanup status.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_cache(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /cache — In-process cache statistics (Sprint 4).
    """
    cache: "CacheManager | None" = (
        context.bot_data.get("cache") if context.bot_data else None
    )
    if not cache:
        await update.message.reply_text("⚠️ Cache not available.")
        return

    info = cache.info()
    total = info.get("total_entries", 0)
    expired = info.get("expired_entries", 0)
    live = total - expired

    text = (
        f"📦 <b>Cache</b>\n\n"
        f"Total entries:   {total}\n"
        f"Live entries:    {live}\n"
        f"Expired entries: {expired}\n\n"
        f"<i>Expired entries are evicted on the next maintenance cycle.\n"
        f"Use /health to see maintenance schedule.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ── Registration ──────────────────────────────────────────────────────────────

def register(
    application: Application,
    authorized_user_ids: list[int],
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
    Register all command handlers on the given Application.

    Runtime singletons are stored in ``bot_data`` so that handlers can
    access live data without globals.
    """
    application.bot_data["provider_manager"] = provider_manager
    application.bot_data["watchlist"] = watchlist
    application.bot_data["scanner"] = scanner
    application.bot_data["db"] = db
    application.bot_data["ranking_engine"] = ranking_engine
    application.bot_data["market_mode_detector"] = market_mode_detector
    application.bot_data["heartbeat"] = heartbeat
    # Sprint 4
    application.bot_data["cache"] = cache
    application.bot_data["start_time"] = start_time

    auth = _authorised(authorized_user_ids)

    handlers: list[tuple[str, Callable]] = [
        # Sprint 1 / 2
        ("start",       auth(_cmd_start)),
        ("help",        auth(_cmd_help)),
        ("ping",        auth(_cmd_ping)),
        ("version",     auth(_cmd_version)),
        ("stats",       auth(_cmd_stats)),
        ("watch",       auth(_cmd_watch)),
        ("diagnostics", auth(_cmd_diagnostics)),
        # Sprint 3
        ("watchlist",   auth(_cmd_watchlist)),
        ("token",       auth(_cmd_token)),
        ("leaderboard", auth(_cmd_leaderboard)),
        ("heartbeat",   auth(_cmd_heartbeat)),
        ("marketmode",  auth(_cmd_marketmode)),
        ("editfilters", auth(_cmd_editfilters)),
        # Sprint 4
        ("health",      auth(_cmd_health)),
        ("providers",   auth(_cmd_providers)),
        ("runtime",     auth(_cmd_runtime)),
        ("database",    auth(_cmd_database)),
        ("cache",       auth(_cmd_cache)),
    ]

    for command, handler_func in handlers:
        application.add_handler(CommandHandler(command, handler_func))
        logger.debug("Registered command handler: /%s", command)

    logger.info("All Telegram command handlers registered (%d total).", len(handlers))


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"
