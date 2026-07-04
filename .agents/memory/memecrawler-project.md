---
name: MemeCrawler project
description: Python 3.12 FastAPI Telegram bot for Solana memecoin research — Sprint 3 complete.
---

## Overview
- **Runtime:** Python 3.12, FastAPI + uvicorn, aiosqlite, python-telegram-bot 21+, pydantic-settings
- **Workflow:** "MemeCrawler API" → `cd memecrawler && python run.py` on port 8000
- **DB:** SQLite at `memecrawler/memecrawler.db`
- **Dependencies:** `pip install -r memecrawler/requirements.txt`

## Sprint status
- Sprint 1: architecture (logging, settings, FastAPI skeleton, providers)
- Sprint 2: full scan pipeline (discovery, watchlist, token_scanner, scheduler, heartbeat stub)
- Sprint 3: **complete** — intelligence layer (scoring, alert, ranking, milestone, market mode)

## Architecture rules (must not violate)
- `DatabaseManager.execute()` returns `(lastrowid, rowcount)`, NOT a cursor
- Alert dedup: SELECT-before-INSERT within `async with self._db.cursor() as cur:` — single-cursor context (SQLite single-connection serialised)
- `MilestoneTracker._update_peak()` formula: `(peak - entry) / max(entry, 1e-9) * 100`
- Outcomes row seeded at **alert dispatch time** (in `AlertEngine._try_insert_alert`) with the alert-time price, NOT lazily on first tracking scan
- TRACKING tokens are excluded from `get_due_tokens()` — milestone checks run via `_run_tracking_milestones()` in the scanner's `run_cycle()`

## Sprint 3 DB tables
- `evaluations(mint, score, max_score, confidence, risk_level, reasons, details, market_mode, scan_count, evaluated_at)`
- `rankings(mint UNIQUE, symbol, score, confidence, risk_level, rank, rank_type, ranked_at)`
- `outcomes(mint UNIQUE, alert_id, entry_price_usd, peak_price_usd, current_price_usd, peak_gain_pct, current_gain_pct, outcome, ...)`
- Sprint 3 migrations add `score, confidence, risk_level, alert_sent_at` columns to `watchlist`

## Telegram commands
Sprint 1/2: /start /help /ping /version /stats /watch /diagnostics
Sprint 3 added: /watchlist /token /leaderboard /heartbeat /marketmode /editfilters

## WatchEntry model
Sprint 3 added optional fields to `WatchEntry` (models/token.py): `score`, `confidence`, `risk_level`, `alert_sent_at`. Mapped in `_row_to_entry()` in watchlist.py.

## Settings runtime overrides
`settings.py` has `_runtime_overrides` dict + `get/set/clear_runtime_override()` helpers for `/editfilters` in-process mutation.

## Key file locations
- `app/analysis/` — Sprint 3 intelligence layer
- `app/analysis/scorer.py` — orchestrates engines, normalises score 0-100
- `app/analysis/engines/` — trend, liquidity, market_cap, volume, buy_pressure, stability, age, social, risk, confidence
- `app/scanner/token_scanner.py` — `set_intelligence_context()` + `_run_intelligence()` + `_run_tracking_milestones()`
- `app/heartbeat/heartbeat.py` — `set_runtime_context()` for runtime singletons injection
