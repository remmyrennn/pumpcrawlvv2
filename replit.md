# MemeCrawler

A personal Solana memecoin research engine that continuously scans tokens, tracks promising projects over time, evaluates risk and conviction, and only alerts after multiple confirmations.

## Run & Operate

- `cd memecrawler && python run.py` — start the FastAPI server (port 8000)
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Copy `memecrawler/.env.example` → `memecrawler/.env` and fill in your values
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)

## Stack (MemeCrawler)

- Python 3.12
- FastAPI + Uvicorn
- httpx (shared async HTTP client — one instance, no module creates its own)
- SQLite via aiosqlite (WAL mode)
- python-telegram-bot (async, v21)
- Pydantic Settings (loads from .env)
- tenacity (retry logic)
- Supabase (prepared, Sprint 4)

## Where things live

```
memecrawler/
├── app/
│   ├── main.py          # FastAPI app + lifespan startup/shutdown
│   ├── logger.py        # centralised logging — call setup_logging() once
│   ├── http_client.py   # shared httpx.AsyncClient singleton
│   ├── config/          # Pydantic Settings (.env)
│   ├── database/        # SQLite manager, all DDL, schema
│   ├── providers/       # base interface + DexScreener/Pumpfun/Helius/RugCheck/RPC
│   ├── scanner/         # Scheduler framework (no scan logic until Sprint 2)
│   ├── telegram/        # bot.py (lifecycle) + handlers.py (commands)
│   ├── heartbeat/       # periodic liveness tick (message sending: Sprint 2)
│   ├── cache/           # in-process TTL cache
│   ├── analysis/        # scoring/risk (Sprint 2+)
│   ├── supabase/        # cloud sync stub (Sprint 4)
│   └── utils/           # formatting, time, retry, validation, errors, http
├── logs/
├── tests/
├── run.py               # entry point — uvicorn
├── requirements.txt
└── .env.example
```

## Architecture decisions

- **Single HTTP client**: `app.http_client` owns one `httpx.AsyncClient`. No module creates its own. Call `get_http_client()` to use it.
- **Provider interface**: all data sources implement `BaseProvider` with `health_check()`. The `ProviderManager` dispatches; providers are interchangeable.
- **Scheduler isolation**: each job runs in its own asyncio task; exceptions never kill other jobs.
- **Configuration via Pydantic Settings**: all values from `.env`, never hardcoded. Feature flags control which providers are active.
- **Supabase deferred**: `SupabaseClient` is a Sprint 4 stub — `connect()` is a no-op until then.

## Sprint Roadmap

- **Sprint 1 (done)**: Foundation — FastAPI, logger, HTTP client, SQLite, providers (framework), Telegram bot (/start /help /ping /version /stats), scheduler, heartbeat, cache, Supabase stub.
- **Sprint 2 (done)**: Provider data fetch, token scanner, watchlist management, alert dispatch.
- **Sprint 3**: Multi-signal conviction scoring, risk evaluation, alert thresholds.
- **Sprint 4**: Supabase sync, export, cloud backup.

## Required Secrets

- `BOT_TOKEN` — Telegram bot token from @BotFather (required for bot)
- `AUTHORIZED_USERS` — comma-separated Telegram user IDs
- `TARGET_CHAT` — Telegram chat ID for alerts
- `HELIUS_API_KEY` — only if `ENABLE_HELIUS=true`
- `SUPABASE_URL` / `SUPABASE_KEY` — Sprint 4 only

## Gotchas

- **Startup order matters**: logger → config → DB → HTTP client → providers → Telegram → scheduler. This order is enforced in `lifespan()`.
- **Never use `datetime.now()`** — always import `utcnow()` from `app.utils.time_utils`.
- **Never use `console.log` / `print`** — use `logging.getLogger(__name__)`.
- **Python CWD**: the workflow runs from `memecrawler/` — relative paths (e.g. `memecrawler.db`) resolve there.

## Stack (Node/TypeScript workspace)

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
