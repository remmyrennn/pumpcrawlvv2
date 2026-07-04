"""
Telegram command handlers.

Every user-facing command is implemented here as a standalone async
function. The :func:`register` function wires them to the Application so
that ``bot.py`` stays focused on lifecycle management.

Sprint 1 commands: /start, /help, /ping, /version, /stats
Sprint 2 commands: /watch, /diagnostics  (+ /stats updated with real data)
Sprint 3 commands: /leaderboard, /watchlist, /token, /heartbeat,
                   /marketmode, /editfilters
"""

from __future__ import annotations

import logging
import platform
from functools import wraps
from typing import TYPE_CHECKING, Callable, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import app as app_module
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.analysis.market_mode import MarketModeDetector
    from app.analysis.ranking import RankingEngine
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
        "🔍 <b>MemeCrawler — Command Reference</b>\n\n"
        "<b>Information</b>\n"
        "/start        — Welcome message\n"
        "/help         — This reference\n"
        "/ping         — Check bot liveness\n"
        "/version      — Module and sprint versions\n"
        "/stats        — Live system statistics\n"
        "/diagnostics  — Provider health details\n\n"
        "<b>Watchlist &amp; Scoring</b>\n"
        "/watch        — Watchlist grouped by priority (Sprint 2)\n"
        "/watchlist    — Watchlist with score/confidence data\n"
        "/token &lt;mint&gt; — Detailed token evaluation\n"
        "/leaderboard  — Top tokens by conviction/confidence\n\n"
        "<b>Market Intelligence</b>\n"
        "/heartbeat    — Manual status report\n"
        "/marketmode   — Current market mode (BULL/NEUTRAL/WEAK)\n\n"
        "<b>Configuration</b>\n"
        "/editfilters  — View or adjust runtime filters\n\n"
        "<i>Sprint 4 will add: /sync, /export, cloud backup commands</i>"
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
    """Display the watchlist grouped by scan priority."""
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )

    if watchlist is None:
        await update.message.reply_text("⚠️ Watchlist is not available.", parse_mode="HTML")
        return

    try:
        entries = await watchlist.get_all(include_archived=False)
    except Exception as exc:
        logger.error("Failed to read watchlist for /watch: %s", exc)
        await update.message.reply_text("❌ Failed to read watchlist.", parse_mode="HTML")
        return

    if not entries:
        await update.message.reply_text(
            "📋 <b>Watchlist is empty.</b>\n\nThe scanner hasn't discovered any tokens yet.",
            parse_mode="HTML",
        )
        return

    from app.models.token import ScanPriority
    groups: dict[str, list] = {p.value: [] for p in ScanPriority}
    for entry in entries:
        groups[entry.priority.value].append(entry)

    lines: list[str] = [f"📋 <b>Watchlist — {len(entries)} token(s)</b>\n"]
    priority_icons = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}

    for priority_name in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        bucket = groups.get(priority_name, [])
        if not bucket:
            continue
        icon = priority_icons.get(priority_name, "⚪")
        lines.append(f"\n{icon} <b>{priority_name}</b> ({len(bucket)})")
        for entry in bucket[:10]:
            mc = _fmt_usd(entry.market_cap_usd)
            liq = _fmt_usd(entry.liquidity_usd)
            symbol = entry.symbol or entry.mint[:8]
            lines.append(
                f"  • <code>{symbol}</code>  MC:{mc}  Liq:{liq}"
                f"  [{entry.state.value}]  scans:{entry.scan_count}"
            )
        if len(bucket) > 10:
            lines.append(f"  <i>…and {len(bucket) - 10} more</i>")

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"
    await update.message.reply_text(message, parse_mode="HTML")


async def _cmd_diagnostics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider_manager: "ProviderManager | None" = (
        context.bot_data.get("provider_manager") if context.bot_data else None
    )
    scanner: "TokenScanner | None" = (
        context.bot_data.get("scanner") if context.bot_data else None
    )
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )

    lines: list[str] = ["🔧 <b>MemeCrawler — Diagnostics</b>\n"]

    lines.append("<b>Providers</b>")
    if provider_manager:
        for p in provider_manager.all():
            info = p.info()
            status_icon = "✅" if p.is_healthy else ("⚠️" if info["error_count"] < 3 else "❌")
            latency = f"{info['latency_ms']:.0f}ms" if info["latency_ms"] is not None else "—"
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

    lines.append("\n<b>Scanner</b>")
    if scanner:
        s = scanner.info()
        avg = s.get("avg_scan_time_ms")
        avg_str = f"{avg:.0f}ms" if avg is not None else "—"
        lines.append(
            f"  Cycles:    {s['cycles']}\n"
            f"  Scanned:   {s['tokens_scanned']}\n"
            f"  Errors:    {s['scan_errors']}\n"
            f"  Avg scan:  {avg_str}"
        )
    else:
        lines.append("  — scanner not available —")

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


# ── Sprint 3 handlers ─────────────────────────────────────────────────────────

async def _cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /watchlist — Display watchlist with score and confidence data from Sprint 3.
    """
    watchlist: "WatchlistManager | None" = (
        context.bot_data.get("watchlist") if context.bot_data else None
    )
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )

    if watchlist is None:
        await update.message.reply_text("⚠️ Watchlist is not available.", parse_mode="HTML")
        return

    try:
        entries = await watchlist.get_all(include_archived=False)
    except Exception as exc:
        logger.error("Failed to read watchlist for /watchlist: %s", exc)
        await update.message.reply_text("❌ Failed to read watchlist.", parse_mode="HTML")
        return

    if not entries:
        await update.message.reply_text(
            "📋 <b>Watchlist is empty.</b>\n\nNo tokens discovered yet.",
            parse_mode="HTML",
        )
        return

    # Sort by score descending (score may be None for tokens not yet scored)
    def _sort_key(e: object) -> float:
        return getattr(e, "score", None) or -1.0

    sorted_entries = sorted(entries, key=_sort_key, reverse=True)

    lines: list[str] = [f"📊 <b>Watchlist — {len(entries)} token(s)</b>\n"]
    risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "💀", "UNKNOWN": "⚪"}

    for entry in sorted_entries[:20]:
        symbol = entry.symbol or entry.mint[:8]
        score = getattr(entry, "score", None)
        conf = getattr(entry, "confidence", None)
        risk = getattr(entry, "risk_level", "UNKNOWN")

        score_str = f"{score:.0f}" if score is not None else "—"
        conf_str = f"{conf:.0f}%" if conf is not None else "—"
        risk_str = risk_icon.get(risk, "⚪")

        lines.append(
            f"  <code>{symbol:>10}</code> "
            f"score:<b>{score_str:>3}</b> conf:{conf_str:>5} "
            f"{risk_str} [{entry.state.value}]"
        )

    if len(entries) > 20:
        lines.append(f"\n<i>…and {len(entries) - 20} more tokens</i>")

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"
    await update.message.reply_text(message, parse_mode="HTML")


async def _cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /token <mint> — Detailed evaluation for a specific token.
    """
    db: "DatabaseManager | None" = (
        context.bot_data.get("db") if context.bot_data else None
    )

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /token <mint_address>", parse_mode="HTML"
        )
        return

    mint = args[0].strip()

    if db is None:
        await update.message.reply_text("⚠️ Database not available.", parse_mode="HTML")
        return

    # Watchlist row
    wl_row = await db.fetchone("SELECT * FROM watchlist WHERE mint = ?", (mint,))
    if not wl_row:
        await update.message.reply_text(
            f"❓ Token <code>{mint[:16]}…</code> not found in watchlist.",
            parse_mode="HTML",
        )
        return

    wl = dict(wl_row)

    # Latest evaluation
    eval_row = await db.fetchone(
        """
        SELECT score, confidence, risk_level, reasons, market_mode, scan_count,
               evaluated_at
        FROM evaluations
        WHERE mint = ?
        ORDER BY evaluated_at DESC
        LIMIT 1
        """,
        (mint,),
    )

    symbol = wl.get("symbol") or mint[:8]
    name = wl.get("name") or "Unknown"
    state = wl.get("state", "UNKNOWN")

    lines = [
        f"🔍 <b>Token: {symbol}</b> ({name})\n",
        f"<code>{mint}</code>\n",
        f"<b>State:</b>  {state}",
        f"<b>Priority:</b> {wl.get('priority', '—')}",
        f"<b>Scans:</b>  {wl.get('scan_count', 0)}",
        f"<b>MC:</b>     {_fmt_usd(wl.get('market_cap_usd'))}",
        f"<b>Liq:</b>    {_fmt_usd(wl.get('liquidity_usd'))}",
        f"<b>Vol 24h:</b> {_fmt_usd(wl.get('volume_24h_usd'))}",
    ]

    if eval_row:
        import json
        ev = dict(eval_row)
        try:
            reasons = json.loads(ev.get("reasons") or "[]")
        except Exception:
            reasons = []

        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "💀", "UNKNOWN": "⚪"}
        ri = risk_icon.get(ev.get("risk_level", "UNKNOWN"), "⚪")

        lines.extend([
            "",
            "<b>Latest Evaluation</b>",
            f"<b>Score:</b>      {ev['score']:.1f}/100",
            f"<b>Confidence:</b> {ev['confidence']:.0f}%",
            f"<b>Risk:</b>       {ri} {ev.get('risk_level', '—')}",
            f"<b>Market:</b>     {ev.get('market_mode', '—')}",
            f"<b>At:</b>         {str(ev.get('evaluated_at', ''))[:19]}",
        ])

        if reasons:
            lines.append("\n<b>Signals:</b>")
            for r in reasons[:5]:
                lines.append(f"  • {r}")
    else:
        lines.append("\n<i>No evaluation yet — token not yet scored.</i>")

    # Outstanding milestones
    milestone_rows = await db.fetchall(
        "SELECT kind, value, achieved_at FROM milestones WHERE mint = ? ORDER BY achieved_at DESC LIMIT 5",
        (mint,),
    )
    if milestone_rows:
        lines.append("\n<b>Milestones:</b>")
        for m in milestone_rows:
            lines.append(
                f"  • {dict(m).get('kind', '?')}  "
                f"({dict(m).get('achieved_at', '')[:10]})"
            )

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"
    await update.message.reply_text(message, parse_mode="HTML")


async def _cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /leaderboard — Top tokens by conviction, confidence, and improvement.
    """
    ranking: "RankingEngine | None" = (
        context.bot_data.get("ranking_engine") if context.bot_data else None
    )

    if ranking is None:
        await update.message.reply_text(
            "⚠️ Ranking engine not available (Sprint 3 required).", parse_mode="HTML"
        )
        return

    args = context.args or []
    rank_type = args[0].lower() if args else "conviction"
    if rank_type not in ("conviction", "confidence", "improvement"):
        rank_type = "conviction"

    try:
        if rank_type == "improvement":
            rows = await ranking.get_improvement_top(n=10)
        else:
            rows = await ranking.get_top(n=10, rank_type=rank_type)
    except Exception as exc:
        logger.error("Leaderboard fetch failed: %s", exc)
        await update.message.reply_text("❌ Failed to load leaderboard.", parse_mode="HTML")
        return

    if not rows:
        await update.message.reply_text(
            "📭 No ranked tokens yet. Wait for the scorer to run.",
            parse_mode="HTML",
        )
        return

    type_label = {"conviction": "Conviction Score", "confidence": "Confidence", "improvement": "Score Improvement"}
    risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "💀", "UNKNOWN": "⚪"}

    lines = [f"🏆 <b>Leaderboard — Top {len(rows)} by {type_label.get(rank_type, rank_type)}</b>\n"]

    for i, row in enumerate(rows, start=1):
        symbol = row.get("symbol") or (row.get("mint") or "")[:8]
        score = row.get("score") or row.get("latest_score") or 0.0
        conf = row.get("confidence") or 0.0
        risk = row.get("risk_level") or "UNKNOWN"
        ri = risk_icon.get(risk, "⚪")

        if rank_type == "improvement":
            improvement = row.get("improvement") or 0.0
            detail = f"Δ{improvement:+.1f}"
        else:
            detail = f"conf {conf:.0f}%"

        lines.append(
            f"<b>{i}.</b> <code>{symbol:>10}</code>  "
            f"score <b>{score:.1f}</b>  {detail}  {ri}"
        )

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3950] + "\n<i>…truncated</i>"
    await update.message.reply_text(message, parse_mode="HTML")


async def _cmd_heartbeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /heartbeat — Manually trigger a full status report.
    """
    heartbeat: "Heartbeat | None" = (
        context.bot_data.get("heartbeat") if context.bot_data else None
    )

    if heartbeat is None:
        await update.message.reply_text(
            "⚠️ Heartbeat not available.", parse_mode="HTML"
        )
        return

    await update.message.reply_text("⏳ Building status report…")
    try:
        await heartbeat.tick()
    except Exception as exc:
        logger.error("/heartbeat command failed: %s", exc)
        await update.message.reply_text(f"❌ Heartbeat error: {exc}", parse_mode="HTML")


async def _cmd_marketmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /marketmode — Show the current aggregate market sentiment.
    """
    market_mode: "MarketModeDetector | None" = (
        context.bot_data.get("market_mode_detector") if context.bot_data else None
    )

    if market_mode is None:
        await update.message.reply_text(
            "⚠️ Market mode detector not available (Sprint 3 required).",
            parse_mode="HTML",
        )
        return

    info = market_mode.info()
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
