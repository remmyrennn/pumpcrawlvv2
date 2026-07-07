---
name: MemeCrawler Sprint 5 fixes
description: 10 audit bugs fixed, Helius wired as fallback, real Supabase sync implemented. All credentials hardcoded in settings.py defaults.
---

## Project
Python/FastAPI Telegram bot at `/home/runner/workspace/memecrawler/`.
Entry point: `run.py` → `uvicorn app.main:app`.
No workflow registered yet — bot starts as a standalone Python process.

## Credential convention
All credentials hardcoded as pydantic Field defaults in `app/config/settings.py`.
User explicitly does NOT want env vars. Helius key left as "" — user must paste it into `helius_api_key` default.

## Bugs fixed (Sprint 5)

1. **risk.py** — mintAuthority/freezeAuthority double-penalised (-11 instead of -6). Removed inline deduction; only `_extract_flags()` adds them now.
2. **market_mode.py** — `weak_ratio` variable computed but never used; `bull_ratio` now drives both BULL and WEAK checks.
3. **ranking.py** — `symbol` hardcoded as "" in `_upsert_rank`. Added `symbol` param to `update()`. `_refresh_ordinal_ranks` was O(n²); now O(n) via single cursor block.
4. **handlers.py** `/leaderboard` — always queried `ORDER BY score DESC` regardless of sort_key arg. Fixed to use `ranking_engine.get_top(rank_type=...)` / `get_improvement_top()`.
5. **token_scanner.py** — TRACKING milestone fired twice (once in `_scan_token`, once in `_run_tracking_milestones`). Removed duplicate in `_scan_token`.
6. **database/manager.py** `backup()` — used `shutil.copy2` on WAL database (could produce corrupt backup). Fixed to use `sqlite3.backup()` API with WAL checkpoint.
7. **discovery/engine.py** — `blacklisted_developers` never enforced. Added check in `_reject_reason()` via `getattr(token, 'creator', None)`.
8. **discovery/engine.py** — `min_liquidity_usd` read at construction time (ignores /editfilters). Fixed to read `get_runtime_override()` dynamically.
9. **scorer.py** `_is_eligible()` — `min_alert_score/confidence/scans` ignored runtime overrides. Fixed to call `get_runtime_override()` first.
10. **alert_engine.py** — `_build_alert_message()` called twice (once to store in DB, once to send). `_try_insert_alert()` now returns `Optional[str]` (the built message) instead of `bool`.

## Sprint 5 additions
- Helius wired into DiscoveryEngine as third provider (`helius=_helius_provider`).
- Helius `get_token_metadata()` added as DexScreener fallback in token scanner.
- Helius `get_new_tokens()` is safe placeholder (returns [] — would need webhook for real new-token discovery).
- Pump.fun fallback in `_fetch_token_data` replaced with Helius.
- `effective_enable_helius` property auto-enables Helius when API key is non-empty.
- Real Supabase sync implemented in `supabase/client.py` using supabase-py v2 async client.
  - Incremental watermarks per table (updated_at / sent_at / last_seen_at).
  - `set_db(db)` call added in main.py; sync scheduler job registered when connected.

**Why:** The bugs caused silent data integrity issues (wrong risk scores, blank symbols in leaderboard, duplicate alerts, stale runtime filter values, corrupt backups).
