"""
Telegram command handlers.

Sprint 1 commands: /start, /help, /ping, /version, /stats
Sprint 2 commands: /watch, /diagnostics
Sprint 3 commands: /leaderboard, /watchlist, /token, /heartbeat,
                   /marketmode, /editfilters
Sprint 4 commands: /health, /providers, /runtime, /database, /cache
Sprint 5 commands: /menu, /chats, /broadcast, /sendto
"""

from __future__ import annotations

import logging
import platform
import time
from functools import wraps
from typing import TYPE_CHECKING, Callable, Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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


# ── Inline main menu ──────────────────────────────────────────────────────────

def _main_menu() -> InlineKeyboardMarkup:
    """Return the main inline command menu."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",       callback_data="cmd:stats"),
            InlineKeyboardButton("💓 Heartbeat",   callback_data="cmd:heartbeat"),
            InlineKeyboardButton("🏓 Ping",        callback_data="cmd:ping"),
        ],
        [
            InlineKeyboardButton("👁 Watch",       callback_data="cmd:watch"),
            InlineKeyboardButton("📋 Watchlist",   callback_data="cmd:watchlist"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd:leaderboard"),
        ],
        [
            InlineKeyboardButton("📈 Market",      callback_data="cmd:marketmode"),
            InlineKeyboardButton("⚙️ Filters",     callback_data="cmd:editfilters"),
            InlineKeyboardButton("🔎 Token",       callback_data="cmd:token"),
        ],
        [
            InlineKeyboardButton("🏥 Health",      callback_data="cmd:health"),
            InlineKeyboardButton("📡 Providers",   callback_data="cmd:providers"),
            InlineKeyboardButton("⚙️ Runtime",     callback_data="cmd:runtime"),
        ],
        [
            InlineKeyboardButton("💾 Database",    callback_data="cmd:database"),
            InlineKeyboardButton("📦 Cache",       callback_data="cmd:cache"),
            InlineKeyboardButton("📡 Chats",       callback_data="cmd:chats"),
        ],
        [
            InlineKeyboardButton("📣 Broadcast",   callback_data="cmd:broadcast"),
            InlineKeyboardButton("🔬 Diagnostics", callback_data="cmd:diagnostics"),
            InlineKeyboardButton("❓ Help",         callback_data="cmd:help"),
        ],
        [
            InlineKeyboardButton("➕ Add Chat",    callback_data="cmd:addchat"),
            InlineKeyboardButton("➖ Remove Chat", callback_data="cmd:removechat"),
            InlineKeyboardButton("📡 Chats",       callback_data="cmd:chats"),
        ],
    ])


# ── Auth decorator ────────────────────────────────────────────────────────────

def _authorised(authorized_user_ids: list[int]) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_user:
                return
            user_id = update.effective_user.id
            if not authorized_user_ids or user_id not in authorized_user_ids:
                logger.warning(
                    "Unauthorised access attempt by user %d.", user_id
                )
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "⛔ You are not authorised to use MemeCrawler."
                    )
                return
            await func(update, context)
        return wrapper
    return decorator


# ── Callback dispatcher ───────────────────────────────────────────────────────

_CMD_MAP: dict[str, Callable] = {}   # populated in register()


async def _dispatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all inline button taps by dispatching to the matching command handler."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    cmd = (query.data or "").removeprefix("cmd:")
    handler = _CMD_MAP.get(cmd)
    if handler:
        await handler(update, context)
    else:
        await query.message.reply_text(f"⚠️ Unknown action: {cmd}")


# ── Sprint 1 / 2 handlers ─────────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    first_name = user.first_name if user else "Researcher"
    text = (
        f"👋 <b>Welcome to MemeCrawler, {first_name}!</b>\n\n"
        "MemeCrawler is your personal Solana memecoin research engine.\n\n"
        "It continuously scans tokens, evaluates risk, tracks promising "
        "projects, and only alerts you after multiple confirmations.\n\n"
        "<b>Philosophy:</b> Quality over quantity. No sniping. No FOMO.\n\n"
        "Tap any button below to run a command."
    )
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=_main_menu()
    )


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🔍 <b>MemeCrawler — Command Reference</b>\n\n"
        "<b>Information</b>\n"
        "/start        — Welcome + menu\n"
        "/ping         — Check bot liveness\n"
        "/version      — Module versions\n"
        "/stats        — Live system statistics\n"
        "/diagnostics  — Provider health details\n\n"
        "<b>Watchlist &amp; Scoring</b>\n"
        "/watch        — Watchlist by priority\n"
        "/watchlist    — Watchlist with scores\n"
        "/token &lt;mint&gt; — Detailed token eval\n"
        "/leaderboard  — Top tokens\n\n"
        "<b>Market Intelligence</b>\n"
        "/heartbeat    — Manual status report\n"
        "/marketmode   — BULL / NEUTRAL / WEAK\n\n"
        "<b>Monitoring</b>\n"
        "/health       — Subsystem health\n"
        "/providers    — Provider stats\n"
        "/runtime      — Memory &amp; uptime\n"
        "/database     — SQLite stats\n"
        "/cache        — Cache statistics\n\n"
        "<b>Broadcast</b>\n"
        "/chats                        — List broadcast targets\n"
        "/broadcast [text]             — Send heartbeat (or custom text) to ALL groups\n"
        "/sendto &lt;name_or_id&gt; &lt;text&gt;   — Send to one specific group\n"
        "/addchat &lt;id&gt; [name]          — Add a group to broadcast list (persisted)\n"
        "/removechat &lt;id_or_name&gt;      — Remove a group from broadcast list\n\n"
        "<b>Configuration</b>\n"
        "/editfilters  — View/adjust runtime filters\n"
        "/menu         — Reopen this menu"
    )
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=_main_menu()
    )


async def _cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("🏓 Pong! MemeCrawler is online.")


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
        f"• pydantic-settings:   2.6.x\n\n"
        "<b>Providers</b>\n"
        "• DexScreener  v2.0.0\n"
        "• Helius       v1.0.0\n"
        "• RugCheck     v2.0.0\n"
        "• Solana RPC   v2.0.0"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


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
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    if not watchlist:
        await update.effective_message.reply_text("⚠️ Watchlist not available.")
        return

    try:
        entries = await watchlist.get_all()
    except Exception as exc:
        logger.error("_cmd_watch: failed to fetch watchlist: %s", exc)
        await update.effective_message.reply_text("❌ Failed to fetch watchlist data.")
        return

    if not entries:
        await update.effective_message.reply_text(
            "📋 <b>Watchlist is empty.</b>\n\nThe scanner will populate it automatically.",
            parse_mode="HTML",
        )
        return

    by_priority: dict[str, list] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    for entry in entries:
        pri = getattr(entry, "priority", "LOW")
        by_priority.get(pri, by_priority["LOW"]).append(entry)

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

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_diagnostics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    if not provider_manager:
        await update.effective_message.reply_text("⚠️ Provider manager not available.")
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

    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML")


# ── Sprint 3 handlers ─────────────────────────────────────────────────────────

async def _cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    if not watchlist:
        await update.effective_message.reply_text("⚠️ Watchlist not available.")
        return

    try:
        entries = await watchlist.get_all()
    except Exception as exc:
        logger.error("_cmd_watchlist: error: %s", exc)
        await update.effective_message.reply_text("❌ Failed to fetch watchlist data.")
        return

    if not entries:
        await update.effective_message.reply_text(
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

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "⚠️ Usage: /token &lt;mint_address&gt;", parse_mode="HTML"
        )
        return

    mint = args[0].strip()
    if not db:
        await update.effective_message.reply_text("⚠️ Database not available.")
        return

    try:
        wl_row = await db.fetchone("SELECT * FROM watchlist WHERE mint = ?;", (mint,))
        eval_row = await db.fetchone(
            "SELECT * FROM evaluations WHERE mint = ? ORDER BY evaluated_at DESC LIMIT 1;",
            (mint,),
        )
        rank_row = await db.fetchone("SELECT * FROM rankings WHERE mint = ?;", (mint,))
        outcome_row = await db.fetchone("SELECT * FROM outcomes WHERE mint = ?;", (mint,))
    except Exception as exc:
        logger.error("_cmd_token: DB error for %s: %s", mint, exc)
        await update.effective_message.reply_text("❌ Database error.")
        return

    if not wl_row:
        await update.effective_message.reply_text(
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
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    ranking_engine: "RankingEngine | None" = (
        context.bot_data.get("ranking_engine") if context.bot_data else None
    )
    if not db:
        await update.effective_message.reply_text("⚠️ Database not available.")
        return

    args = context.args or []
    sort_key = args[0].lower() if args else "conviction"
    valid_sorts = {"conviction", "confidence", "improvement"}
    if sort_key not in valid_sorts:
        await update.effective_message.reply_text(
            f"⚠️ Unknown sort. Valid options: {', '.join(sorted(valid_sorts))}"
        )
        return

    sort_label = {
        "conviction": "Conviction Score",
        "confidence": "Confidence",
        "improvement": "Score Improvement",
    }[sort_key]

    try:
        if ranking_engine is not None:
            if sort_key == "improvement":
                rows = await ranking_engine.get_improvement_top(n=10)
            else:
                rows = await ranking_engine.get_top(n=10, rank_type=sort_key)
        else:
            order_clause = (
                "confidence DESC, score DESC" if sort_key == "confidence"
                else "score DESC, confidence DESC"
            )
            raw = await db.fetchall(
                f"SELECT * FROM rankings ORDER BY {order_clause} LIMIT 10;"
            )
            rows = [dict(r) for r in raw]
    except Exception as exc:
        logger.error("_cmd_leaderboard: fetch error: %s", exc)
        await update.effective_message.reply_text("❌ Failed to load leaderboard.")
        return

    if not rows:
        await update.effective_message.reply_text(
            "📊 <b>Leaderboard is empty.</b>\n\n"
            "Tokens need at least one scoring cycle to appear here.",
            parse_mode="HTML",
        )
        return

    # Enrich each row with watchlist + outcome detail from DB
    mints = [r.get("mint", "") for r in rows if r.get("mint")]
    detail: dict[str, dict] = {}
    if mints:
        try:
            placeholders = ",".join("?" * len(mints))
            wl_rows = await db.fetchall(
                f"""
                SELECT w.mint, w.symbol, w.first_seen_at, w.market_cap_usd,
                       w.risk_level, w.state,
                       o.peak_gain_pct, o.current_gain_pct, o.outcome
                FROM   watchlist w
                LEFT JOIN outcomes o ON o.mint = w.mint
                WHERE  w.mint IN ({placeholders})
                """,
                tuple(mints),
            )
            for r in wl_rows:
                detail[r["mint"]] = dict(r)
        except Exception:
            pass

    lines = [f"🏆 <b>Leaderboard — Top {len(rows)} by {sort_label}</b>\n"]

    for i, row in enumerate(rows, start=1):
        mint  = row.get("mint", "")
        sym   = row.get("symbol") or detail.get(mint, {}).get("symbol") or "?"
        score = float(row.get("latest_score") or row.get("score") or 0)
        conf  = float(row.get("confidence") or 0)
        risk  = row.get("risk_level") or detail.get(mint, {}).get("risk_level") or "UNKNOWN"
        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(risk, "⚪")

        d = detail.get(mint, {})
        mc_raw   = d.get("market_cap_usd")
        mc_str   = _fmt_usd(mc_raw) if mc_raw else "—"
        first_raw = str(d.get("first_seen_at") or "")[:10]
        first_str = first_raw or "—"

        peak_raw = d.get("peak_gain_pct")
        peak_str = f"{peak_raw:+.1f}%" if peak_raw is not None else "—"
        curr_raw = d.get("current_gain_pct")
        curr_str = f"{curr_raw:+.1f}%" if curr_raw is not None else "—"
        outcome  = d.get("outcome") or ""

        imp_str = ""
        if sort_key == "improvement":
            imp = float(row.get("improvement") or 0)
            imp_str = f"  Δ{imp:+.0f}"

        state = d.get("state") or ""
        state_tag = f" [{state}]" if state else ""

        lines.append(
            f"\n#{i} {risk_icon} <b>{sym}</b>{state_tag}{imp_str}\n"
            f"  Score: {score:.0f}  Conf: {conf:.0f}%  Risk: {risk}\n"
            f"  MC: {mc_str}  First seen: {first_str}\n"
            f"  Peak: {peak_str}  Now: {curr_str}"
            + (f"  ({outcome})" if outcome else "")
        )

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_heartbeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    heartbeat: "Heartbeat | None" = (
        context.bot_data.get("heartbeat") if context.bot_data else None
    )
    if not heartbeat:
        await update.effective_message.reply_text("⚠️ Heartbeat not available.")
        return
    try:
        await heartbeat.tick()
    except Exception as exc:
        logger.error("_cmd_heartbeat: error: %s", exc)
        await update.effective_message.reply_text("❌ Failed to send heartbeat.")


async def _cmd_marketmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    market_mode_detector: "MarketModeDetector | None" = (
        context.bot_data.get("market_mode_detector") if context.bot_data else None
    )
    if not market_mode_detector:
        await update.effective_message.reply_text("⚠️ Market mode detector not available.")
        return

    info = market_mode_detector.info()
    mode = info.get("mode", "NEUTRAL")
    sample = info.get("sample_size", 0)
    updated = str(info.get("last_updated", "unknown"))[:19]
    mode_icon = {"BULL": "🐂", "NEUTRAL": "➡️", "WEAK": "🐻"}.get(mode, "➡️")
    mode_desc = {
        "BULL": "Strong positive momentum across watched tokens.",
        "NEUTRAL": "Mixed signals — no clear directional bias.",
        "WEAK": "Majority of tokens showing negative or flat trend.",
    }

    text = (
        f"📈 <b>Market Mode: {mode_icon} {mode}</b>\n\n"
        f"{mode_desc.get(mode, '')}\n\n"
        f"<b>Sample size:</b> {sample} tokens\n"
        f"<b>Last updated:</b> {updated} UTC\n\n"
        "<i>Refreshed each scanner cycle.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_editfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        lines = ["⚙️ <b>Runtime Filters</b>\n"]
        for key in EDITABLE_KEYS:
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
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 2:
        await update.effective_message.reply_text(
            "⚠️ Usage: /editfilters &lt;key&gt; &lt;value&gt;", parse_mode="HTML"
        )
        return

    key = args[0].lower()
    raw_value = args[1].strip()

    if key not in EDITABLE_KEYS:
        valid = ", ".join(EDITABLE_KEYS.keys())
        await update.effective_message.reply_text(
            f"❌ Unknown filter key: <code>{key}</code>\nValid keys: {valid}",
            parse_mode="HTML",
        )
        return

    if raw_value.lower() == "reset":
        clear_runtime_override(key)
        base = getattr(settings, key, "—")
        await update.effective_message.reply_text(
            f"✅ <b>{key}</b> reset to default: <code>{base}</code>",
            parse_mode="HTML",
        )
        return

    try:
        typed_value = EDITABLE_KEYS[key](raw_value)
    except (ValueError, TypeError):
        await update.effective_message.reply_text(
            f"❌ Invalid value for <b>{key}</b>: <code>{raw_value}</code>",
            parse_mode="HTML",
        )
        return

    set_runtime_override(key, typed_value)
    await update.effective_message.reply_text(
        f"✅ <b>{key}</b> set to <code>{typed_value}</code>",
        parse_mode="HTML",
    )


# ── Sprint 4 handlers ─────────────────────────────────────────────────────────

async def _cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    start_time: Optional[float] = (
        context.bot_data.get("start_time") if context.bot_data else None
    )

    db_ok = db.is_connected if db else False
    healthy_providers = 0
    total_providers = 0
    if provider_manager:
        providers = provider_manager.all()
        total_providers = len(providers)
        healthy_providers = sum(1 for p in providers if p.is_healthy)

    watched = tracked = alerts_today = 0
    if db and db_ok:
        try:
            r = await db.fetchone(
                "SELECT COUNT(*) AS cnt FROM watchlist WHERE state NOT IN ('ARCHIVED','TRACKING');"
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

    uptime_str = format_uptime(start_time) if start_time else "unknown"
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
        "<i>Use /providers, /database, /cache, /runtime for details.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_providers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    if not provider_manager:
        await update.effective_message.reply_text("⚠️ Provider manager not available.")
        return

    providers = provider_manager.all()
    if not providers:
        await update.effective_message.reply_text("⚠️ No providers registered.")
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

    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def _cmd_runtime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    start_time: Optional[float] = (
        context.bot_data.get("start_time") if context.bot_data else None
    )
    py_version = platform.python_version()
    py_impl = platform.python_implementation()
    sys_platform = f"{platform.system()} {platform.machine()}"
    uptime_str = format_uptime(start_time) if start_time else "unknown"

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
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    if not db:
        await update.effective_message.reply_text("⚠️ Database not available.")
        return
    if not db.is_connected:
        await update.effective_message.reply_text("❌ Database is not connected.")
        return

    try:
        counts = await db.table_row_counts()
        size_bytes = await db.db_file_size_bytes()
    except Exception as exc:
        logger.error("_cmd_database: error: %s", exc)
        await update.effective_message.reply_text("❌ Failed to fetch database info.")
        return

    size_str = (
        f"{size_bytes / 1_048_576:.2f} MB" if size_bytes >= 1_048_576
        else f"{size_bytes / 1024:.1f} KB" if size_bytes >= 1024
        else f"{size_bytes} B"
    )
    count_lines = "".join(f"  {t}: {c:,}\n" for t, c in sorted(counts.items()))

    text = (
        f"💾 <b>Database</b>\n\n"
        f"Status:   connected\n"
        f"Path:     {db.path}\n"
        f"Size:     {size_str}\n"
        f"Tables:   {len(counts)}\n\n"
        f"<b>Row counts:</b>\n{count_lines}"
        f"\n<i>Use /health for maintenance status.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def _cmd_cache(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cache: "CacheManager | None" = (
        context.bot_data.get("cache") if context.bot_data else None
    )
    if not cache:
        await update.effective_message.reply_text("⚠️ Cache not available.")
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
        "<i>Expired entries are evicted on the next maintenance cycle.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


# ── Sprint 5 handlers ─────────────────────────────────────────────────────────

async def _cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reopen the inline command menu."""
    await update.effective_message.reply_text(
        "🎛 <b>MemeCrawler — Main Menu</b>\n\nTap a button to run a command.",
        parse_mode="HTML",
        reply_markup=_main_menu(),
    )


async def _cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active broadcast targets."""
    broadcast_chats: list[dict[str, str]] = (
        context.bot_data.get("broadcast_chats") or []
        if context.bot_data else []
    )
    target_chat: str = (
        context.bot_data.get("target_chat", "") if context.bot_data else ""
    )

    lines = ["📡 <b>Broadcast Targets</b>\n"]
    lines.append(f"🏠 <b>Primary:</b> <code>{target_chat or '—'}</code>")

    if broadcast_chats:
        lines.append("\n<b>Extra broadcast chats:</b>")
        for i, chat in enumerate(broadcast_chats, 1):
            lines.append(
                f"  {i}. <b>{chat.get('name', '?')}</b> — <code>{chat.get('id', '?')}</code>"
            )
    else:
        lines.append("\n<i>No extra broadcast chats configured.</i>")

    total = (1 if target_chat else 0) + len(broadcast_chats)
    lines.append(f"\n<b>Total recipients:</b> {total}")
    lines.append(
        "\n<i>Use /broadcast to send to all, or /sendto &lt;name_or_id&gt; &lt;text&gt; for one group.</i>"
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /broadcast [message]

    No args → builds and sends the live heartbeat status to every configured chat.
    With args → broadcasts that custom text to every configured chat.
    """
    heartbeat: "Heartbeat | None" = (
        context.bot_data.get("heartbeat") if context.bot_data else None
    )
    broadcast_chats: list[dict[str, str]] = (
        context.bot_data.get("broadcast_chats") or []
        if context.bot_data else []
    )
    target_chat: str = (
        context.bot_data.get("target_chat", "") if context.bot_data else ""
    )

    args = context.args or []
    if args:
        text = " ".join(args)
    else:
        if not heartbeat:
            await update.effective_message.reply_text("⚠️ Heartbeat not available.")
            return
        try:
            text = await heartbeat.build_message()
        except Exception as exc:
            await update.effective_message.reply_text(f"❌ Failed to build heartbeat: {exc}")
            return

    # Collect all unique recipients
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []
    if target_chat:
        targets.append((target_chat, "primary"))
        seen.add(target_chat)
    for chat in broadcast_chats:
        cid = chat.get("id", "")
        if cid and cid not in seen:
            targets.append((cid, chat.get("name", cid)))
            seen.add(cid)

    if not targets:
        await update.effective_message.reply_text("⚠️ No broadcast targets configured. Use /chats to check.")
        return

    ok = 0
    failed: list[str] = []
    for cid, label in targets:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            ok += 1
        except Exception as exc:
            logger.error("Broadcast failed for %s (%s): %s", label, cid, exc)
            failed.append(label)

    status = f"✅ Broadcast sent to {ok}/{len(targets)} chat(s)."
    if failed:
        status += f"\n⚠️ Failed: {', '.join(failed)}"
    await update.effective_message.reply_text(status)


async def _cmd_addchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addchat <id> [name]

    Adds a chat to the broadcast list at runtime and persists it to the DB
    so it survives restarts.
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    broadcast_chats: list[dict[str, str]] = (
        context.bot_data.get("broadcast_chats") or []
        if context.bot_data else []
    )

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "⚠️ Usage: /addchat &lt;chat_id&gt; [name]\n\n"
            "Example: /addchat -1001234567890 Alpha Calls",
            parse_mode="HTML",
        )
        return

    chat_id = args[0].strip()
    name = " ".join(args[1:]).strip() if len(args) > 1 else chat_id

    # Check if already present
    existing_ids = {c.get("id", "") for c in broadcast_chats}
    if chat_id in existing_ids:
        await update.effective_message.reply_text(
            f"ℹ️ Chat <code>{chat_id}</code> is already in the broadcast list.",
            parse_mode="HTML",
        )
        return

    # Add to live in-memory list
    new_entry: dict[str, str] = {"id": chat_id, "name": name}
    broadcast_chats.append(new_entry)
    context.bot_data["broadcast_chats"] = broadcast_chats

    # Persist to SQLite
    if db:
        try:
            from app.utils.time_utils import utcnow_iso as _now
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_chats (
                    chat_id  TEXT PRIMARY KEY,
                    name     TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL DEFAULT ''
                );
                """,
            )
            await db.execute(
                "INSERT OR REPLACE INTO broadcast_chats (chat_id, name, added_at) VALUES (?,?,?);",
                (chat_id, name, _now()),
            )
        except Exception as exc:
            logger.error("_cmd_addchat: DB persist failed: %s", exc)
            await update.effective_message.reply_text(
                f"⚠️ Added to session but DB persist failed: {exc}"
            )
            return

    await update.effective_message.reply_text(
        f"✅ Added <b>{name}</b> (<code>{chat_id}</code>) to broadcast list.\n"
        f"Total chats: {len(broadcast_chats)}",
        parse_mode="HTML",
    )


async def _cmd_removechat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removechat <id_or_name>

    Removes a chat from the broadcast list and deletes it from the DB.
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )
    broadcast_chats: list[dict[str, str]] = (
        context.bot_data.get("broadcast_chats") or []
        if context.bot_data else []
    )

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "⚠️ Usage: /removechat &lt;chat_id_or_name&gt;\n\n"
            "Use /chats to see current broadcast targets.",
            parse_mode="HTML",
        )
        return

    query = " ".join(args).strip().lower()

    # Find matching entry
    match: Optional[dict[str, str]] = None
    for chat in broadcast_chats:
        if chat.get("id", "").lower() == query or chat.get("name", "").lower() == query:
            match = chat
            break

    if not match:
        await update.effective_message.reply_text(
            f"❌ No broadcast chat found matching <code>{query}</code>.\n"
            "Use /chats to see all targets.",
            parse_mode="HTML",
        )
        return

    # Remove from live list
    broadcast_chats[:] = [c for c in broadcast_chats if c.get("id") != match.get("id")]
    context.bot_data["broadcast_chats"] = broadcast_chats

    # Remove from DB
    if db:
        try:
            await db.execute(
                "DELETE FROM broadcast_chats WHERE chat_id = ?;",
                (match.get("id", ""),),
            )
        except Exception as exc:
            logger.error("_cmd_removechat: DB delete failed: %s", exc)

    await update.effective_message.reply_text(
        f"🗑 Removed <b>{match.get('name', match.get('id'))}</b> from broadcast list.\n"
        f"Remaining chats: {len(broadcast_chats)}",
        parse_mode="HTML",
    )


async def _cmd_sendto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /sendto <name_or_id> <message>

    Sends a custom message to a single group identified by its name or chat ID.
    The name must match (case-insensitive) one of the entries shown in /chats.
    """
    broadcast_chats: list[dict[str, str]] = (
        context.bot_data.get("broadcast_chats") or []
        if context.bot_data else []
    )
    target_chat: str = (
        context.bot_data.get("target_chat", "") if context.bot_data else ""
    )

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "⚠️ Usage: /sendto &lt;name_or_id&gt; &lt;message text&gt;\n\n"
            "Use /chats to see available targets.",
            parse_mode="HTML",
        )
        return

    target_arg = args[0].strip()
    text = " ".join(args[1:])

    # Build lookup: name → id
    all_chats: dict[str, str] = {}
    if target_chat:
        all_chats["primary"] = target_chat
        all_chats[target_chat] = target_chat
    for chat in broadcast_chats:
        cid = chat.get("id", "")
        name = chat.get("name", cid).lower()
        if cid:
            all_chats[name] = cid
            all_chats[cid] = cid

    resolved_id = all_chats.get(target_arg.lower()) or all_chats.get(target_arg)
    if not resolved_id:
        known = ", ".join(
            f"{c.get('name', c.get('id', '?'))}" for c in broadcast_chats
        ) or "none"
        await update.effective_message.reply_text(
            f"❌ Unknown target: <code>{target_arg}</code>\n"
            f"Known chats: {known}\n\n"
            "Use /chats to see all targets.",
            parse_mode="HTML",
        )
        return

    try:
        await context.bot.send_message(
            chat_id=resolved_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await update.effective_message.reply_text(
            f"✅ Message sent to <code>{target_arg}</code>.", parse_mode="HTML"
        )
    except Exception as exc:
        logger.error("_cmd_sendto: failed to send to %s: %s", resolved_id, exc)
        await update.effective_message.reply_text(
            f"❌ Failed to send: {exc}"
        )


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
    broadcast_chats: Optional[list[dict[str, str]]] = None,
    target_chat: str = "",
) -> None:
    """Register all command handlers on the given Application."""
    application.bot_data["provider_manager"] = provider_manager
    application.bot_data["watchlist"] = watchlist
    application.bot_data["scanner"] = scanner
    application.bot_data["db"] = db
    application.bot_data["ranking_engine"] = ranking_engine
    application.bot_data["market_mode_detector"] = market_mode_detector
    application.bot_data["heartbeat"] = heartbeat
    application.bot_data["cache"] = cache
    application.bot_data["start_time"] = start_time
    application.bot_data["broadcast_chats"] = broadcast_chats or []
    application.bot_data["target_chat"] = target_chat

    auth = _authorised(authorized_user_ids)

    # Command → handler mapping (also used by the callback dispatcher)
    cmd_handlers: list[tuple[str, Callable]] = [
        ("start",        auth(_cmd_start)),
        ("help",         auth(_cmd_help)),
        ("ping",         auth(_cmd_ping)),
        ("version",      auth(_cmd_version)),
        ("stats",        auth(_cmd_stats)),
        ("watch",        auth(_cmd_watch)),
        ("diagnostics",  auth(_cmd_diagnostics)),
        ("watchlist",    auth(_cmd_watchlist)),
        ("token",        auth(_cmd_token)),
        ("leaderboard",  auth(_cmd_leaderboard)),
        ("heartbeat",    auth(_cmd_heartbeat)),
        ("marketmode",   auth(_cmd_marketmode)),
        ("editfilters",  auth(_cmd_editfilters)),
        ("health",       auth(_cmd_health)),
        ("providers",    auth(_cmd_providers)),
        ("runtime",      auth(_cmd_runtime)),
        ("database",     auth(_cmd_database)),
        ("cache",        auth(_cmd_cache)),
        ("menu",         auth(_cmd_menu)),
        ("chats",        auth(_cmd_chats)),
        ("broadcast",    auth(_cmd_broadcast)),
        ("sendto",       auth(_cmd_sendto)),
        ("addchat",      auth(_cmd_addchat)),
        ("removechat",   auth(_cmd_removechat)),
    ]

    # Populate the callback dispatcher map
    _CMD_MAP.update({cmd: func for cmd, func in cmd_handlers})

    for command, handler_func in cmd_handlers:
        application.add_handler(CommandHandler(command, handler_func))

    # Inline button callback handler — dispatches cmd:* callbacks
    application.add_handler(
        CallbackQueryHandler(auth(_dispatch_callback), pattern="^cmd:")
    )

    logger.info("Registered %d command handlers + inline callback dispatcher.", len(cmd_handlers))


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"
