---
name: MemeCrawler project
description: Python 3.12 FastAPI Telegram bot for Solana memecoin research — Sprint 2 complete. Architecture decisions and sharp edges.
---

## Key facts

- All code lives in `memecrawler/` at the workspace root (not inside a TypeScript artifact).
- Runs via workflow "MemeCrawler API": `cd memecrawler && python run.py` on port 8000.
- Python 3.12 installed as Replit module `python-3.12`.

## Architecture rules (never break these)

- **One HTTP client**: `app.http_client.get_http_client()` — no module creates its own.
- **All providers implement `BaseProvider`**: health_check() must catch all exceptions internally.
- **Startup order** enforced in `app/main.py` lifespan: logger → config → DB → HTTP → cache → providers → watchlist → discovery → scanner → telegram → scheduler.
- **Shutdown is fault-isolated**: each teardown step runs in a loop regardless of prior failures.
- **`DatabaseManager.execute()`** returns `(lastrowid, rowcount)` tuple — not a cursor (cursor closes on exit).
- **DB migration ordering**: `_init_schema()` creates tables only; `_apply_migrations()` adds Sprint 2 columns; Sprint 2 indexes are created inside `_apply_migrations()` AFTER columns exist (not in `_init_schema()`).

## Provider health check notes

- Pump.fun returns 530 (Cloudflare) from Replit IPs — expected, handled gracefully.
- RugCheck `/v1/tokens/So11111111.../report/summary` is the health probe (not /stats — returns 404).
- DexScreener and Solana RPC are consistently healthy.

## Sprint 2 modules

- `app/models/token.py` — TokenData, TokenState (9-state machine), ScanPriority (4 tiers), WatchEntry
- `app/discovery/engine.py` — DiscoveryEngine (merge DexScreener + Pump.fun, dedup, filter)
- `app/scanner/watchlist.py` — WatchlistManager (add, get_due_tokens, record_scan, transition_state, update_priority)
- `app/scanner/token_scanner.py` — TokenScanner (run_cycle: discovery + priority-ordered scan)
- `app/providers/dexscreener.py` — get_new_tokens(), get_token_data()
- `app/providers/pumpfun.py` — get_new_coins(), get_token_data()
- `app/providers/rugcheck.py` — get_token_report(), get_token_summary()
- `app/providers/rpc.py` — get_account_info(), get_token_supply()
- `app/providers/base.py` — latency tracking: last_success_at, last_failure_at, latency_ms, total_requests

## Sharp edges / lessons

- **Failed scans MUST set next_scan_at**: In `WatchlistManager.record_scan(token_data=None)`, always set `next_scan_at` based on current priority. Without this, `next_scan_at IS NULL` keeps the token perpetually due and every cycle retries it, wasting API calls.
- **Index ordering matters in SQLite ALTER TABLE**: Sprint 2 indexes on `state`, `priority`, `next_scan_at` must be created AFTER those columns are added via `ALTER TABLE`. Creating them in `_init_schema()` before migrations fails.
- **`AUTHORIZED_USERS` parser** uses `re.fullmatch(r"-?\d+", t)` — accepts valid negative channel IDs (`-100xxx`), rejects malformed tokens like `--123`.
- **Auth is deny-all by default**: Empty `authorized_user_ids` blocks ALL commands. Set `AUTHORIZED_USERS` env var to grant access.
- **Pump.fun is 530 from Replit IPs**: Discovery gracefully returns empty list; scanner falls back to DexScreener.

## Sprint roadmap

- Sprint 1 (done): foundation — FastAPI, DB, HTTP client, providers framework, Telegram bot commands, scheduler, heartbeat, cache.
- Sprint 2 (done): discovery engine, token scanner, watchlist CRUD, state machine, priority system, /watch /stats /diagnostics commands.
- Sprint 3: multi-signal conviction scoring, risk evaluation, alert dispatch (READY_FOR_ALERT → TRACKING).
- Sprint 4: Supabase sync, export.

**Why:** Long-term project with 4 sprints; architecture must not be redesigned between sprints.
