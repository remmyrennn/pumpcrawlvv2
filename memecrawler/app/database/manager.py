"""
SQLite database manager.

Responsibilities
----------------
- Open and close an ``aiosqlite`` connection pool.
- Create all tables on first run (auto-migration).
- Apply additive column migrations on subsequent runs (ALTER TABLE).
- Provide a thin context-manager interface for executing queries.

No business logic lives here. Domain modules handle their own queries
using the connection returned by :meth:`DatabaseManager.connection`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# ── DDL statements ────────────────────────────────────────────────────────────

_CREATE_TOKENS = """
CREATE TABLE IF NOT EXISTS tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT    NOT NULL UNIQUE,
    symbol          TEXT    NOT NULL DEFAULT '',
    name            TEXT    NOT NULL DEFAULT '',
    decimals        INTEGER NOT NULL DEFAULT 9,
    supply          REAL    NOT NULL DEFAULT 0,
    price_usd       REAL,
    market_cap_usd  REAL,
    volume_24h_usd  REAL,
    liquidity_usd   REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT    NOT NULL UNIQUE,
    symbol          TEXT    NOT NULL DEFAULT '',
    name            TEXT    NOT NULL DEFAULT '',
    watch_id        TEXT    NOT NULL DEFAULT '',
    added_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    reason          TEXT    NOT NULL DEFAULT '',
    conviction      INTEGER NOT NULL DEFAULT 0 CHECK(conviction BETWEEN 0 AND 10),
    status          TEXT    NOT NULL DEFAULT 'watching'
                            CHECK(status IN ('watching', 'alerted', 'dismissed', 'archived')),
    notes           TEXT    NOT NULL DEFAULT '',
    expires_at      TEXT,
    state           TEXT    NOT NULL DEFAULT 'DISCOVERED',
    priority        TEXT    NOT NULL DEFAULT 'LOW',
    scan_count      INTEGER NOT NULL DEFAULT 0,
    last_scan_at    TEXT,
    next_scan_at    TEXT,
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    price_usd       REAL,
    market_cap_usd  REAL,
    liquidity_usd   REAL,
    volume_24h_usd  REAL
);
"""

_CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT    NOT NULL,
    alert_type      TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    sent_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    chat_id         TEXT    NOT NULL DEFAULT '',
    telegram_msg_id INTEGER,
    metadata        TEXT    NOT NULL DEFAULT '{}'
);
"""

_CREATE_HISTORY = """
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT    NOT NULL,
    price_usd       REAL,
    market_cap_usd  REAL,
    volume_24h_usd  REAL,
    liquidity_usd   REAL,
    holder_count    INTEGER,
    buys_5m         INTEGER,
    sells_5m        INTEGER,
    age_seconds     REAL,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    source          TEXT    NOT NULL DEFAULT 'unknown'
);
"""

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_MILESTONES = """
CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mint        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    value       REAL    NOT NULL,
    achieved_at TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata    TEXT    NOT NULL DEFAULT '{}'
);
"""

_CREATE_PROVIDERS = """
CREATE TABLE IF NOT EXISTS providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    enabled     INTEGER NOT NULL DEFAULT 1,
    status      TEXT    NOT NULL DEFAULT 'unknown'
                        CHECK(status IN ('unknown', 'healthy', 'degraded', 'down')),
    last_check  TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    metadata    TEXT    NOT NULL DEFAULT '{}'
);
"""

_CREATE_LOGS = """
CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT    NOT NULL,
    module      TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    logged_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata    TEXT    NOT NULL DEFAULT '{}'
);
"""

_CREATE_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mint        TEXT    NOT NULL,
    score       REAL    NOT NULL,
    max_score   REAL    NOT NULL DEFAULT 100.0,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    risk_level  TEXT    NOT NULL DEFAULT 'UNKNOWN',
    reasons     TEXT    NOT NULL DEFAULT '[]',
    details     TEXT    NOT NULL DEFAULT '{}',
    market_mode TEXT    NOT NULL DEFAULT 'NEUTRAL',
    scan_count  INTEGER NOT NULL DEFAULT 0,
    evaluated_at TEXT   NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_RANKINGS = """
CREATE TABLE IF NOT EXISTS rankings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mint        TEXT    NOT NULL UNIQUE,
    symbol      TEXT    NOT NULL DEFAULT '',
    score       REAL    NOT NULL DEFAULT 0.0,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    risk_level  TEXT    NOT NULL DEFAULT 'UNKNOWN',
    rank        INTEGER NOT NULL DEFAULT 0,
    rank_type   TEXT    NOT NULL DEFAULT 'conviction',
    ranked_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_OUTCOMES = """
CREATE TABLE IF NOT EXISTS outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                TEXT    NOT NULL UNIQUE,
    alert_id            INTEGER,
    entry_price_usd     REAL,
    peak_price_usd      REAL,
    current_price_usd   REAL,
    peak_gain_pct       REAL    NOT NULL DEFAULT 0.0,
    current_gain_pct    REAL    NOT NULL DEFAULT 0.0,
    outcome             TEXT    NOT NULL DEFAULT 'TRACKING',
    tracked_since       TEXT    NOT NULL DEFAULT (datetime('now')),
    last_updated        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_ALL_DDL: list[str] = [
    _CREATE_TOKENS,
    _CREATE_WATCHLIST,
    _CREATE_ALERTS,
    _CREATE_HISTORY,
    _CREATE_SETTINGS,
    _CREATE_MILESTONES,
    _CREATE_PROVIDERS,
    _CREATE_LOGS,
    _CREATE_EVALUATIONS,
    _CREATE_RANKINGS,
    _CREATE_OUTCOMES,
]

# ── Indexes ────────────────────────────────────────────────────────────────────
# Sprint 1 indexes — safe to create before any migration (columns always exist).

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tokens_mint ON tokens(mint);",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_mint ON watchlist(mint);",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_mint ON alerts(mint);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at);",
    "CREATE INDEX IF NOT EXISTS idx_history_mint_recorded ON history(mint, recorded_at);",
    "CREATE INDEX IF NOT EXISTS idx_milestones_mint ON milestones(mint);",
    "CREATE INDEX IF NOT EXISTS idx_logs_logged_at ON logs(logged_at);",
]

# Sprint 2 indexes — reference columns that may not yet exist on an existing DB.
# These are applied AFTER the additive column migrations run.
_SPRINT2_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_watchlist_state ON watchlist(state);",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_priority ON watchlist(priority);",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_next_scan ON watchlist(next_scan_at);",
]

# Sprint 3 indexes — applied after Sprint 3 column migrations.
_SPRINT3_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_evaluations_mint ON evaluations(mint);",
    "CREATE INDEX IF NOT EXISTS idx_evaluations_mint_at ON evaluations(mint, evaluated_at);",
    "CREATE INDEX IF NOT EXISTS idx_evaluations_score ON evaluations(score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_rankings_score ON rankings(score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_rankings_mint ON rankings(mint);",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_mint ON outcomes(mint);",
    "CREATE INDEX IF NOT EXISTS idx_milestones_mint_kind ON milestones(mint, kind);",
]

# ── Additive migrations (Sprint 2) ────────────────────────────────────────────
# Each entry is (table, column, column_def). Applied with ALTER TABLE ADD COLUMN;
# ignored (silently) when the column already exists.

_SPRINT2_MIGRATIONS: list[tuple[str, str, str]] = [
    ("watchlist", "symbol",         "TEXT NOT NULL DEFAULT ''"),
    ("watchlist", "name",           "TEXT NOT NULL DEFAULT ''"),
    ("watchlist", "watch_id",       "TEXT NOT NULL DEFAULT ''"),
    ("watchlist", "state",          "TEXT NOT NULL DEFAULT 'DISCOVERED'"),
    ("watchlist", "priority",       "TEXT NOT NULL DEFAULT 'LOW'"),
    ("watchlist", "scan_count",     "INTEGER NOT NULL DEFAULT 0"),
    ("watchlist", "last_scan_at",   "TEXT"),
    ("watchlist", "next_scan_at",   "TEXT"),
    ("watchlist", "first_seen_at",  "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ("watchlist", "last_seen_at",   "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ("watchlist", "price_usd",      "REAL"),
    ("watchlist", "market_cap_usd", "REAL"),
    ("watchlist", "liquidity_usd",  "REAL"),
    ("watchlist", "volume_24h_usd", "REAL"),
    ("history",   "buys_5m",        "INTEGER"),
    ("history",   "sells_5m",       "INTEGER"),
    ("history",   "age_seconds",    "REAL"),
]

# ── Additive migrations (Sprint 3) ────────────────────────────────────────────
_SPRINT3_MIGRATIONS: list[tuple[str, str, str]] = [
    ("watchlist", "score",          "REAL"),
    ("watchlist", "confidence",     "REAL"),
    ("watchlist", "risk_level",     "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
    ("watchlist", "alert_sent_at",  "TEXT"),
    ("milestones", "kind",          "TEXT NOT NULL DEFAULT ''"),
    ("milestones", "metadata",      "TEXT NOT NULL DEFAULT '{}'"),
]


# ── Manager ───────────────────────────────────────────────────────────────────

class DatabaseManager:
    """
    Manages the SQLite connection lifecycle and schema initialisation.

    Attributes
    ----------
    path:
        Filesystem path to the SQLite database file.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Open the database connection and initialise the schema.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._conn is not None:
            logger.debug("Database already connected — skipping.")
            return

        logger.info("Connecting to SQLite database at '%s'.", self.path)
        self._conn = await aiosqlite.connect(self.path)

        # Return rows as dict-like objects.
        self._conn.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent read performance.
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")

        await self._init_schema()
        await self._apply_migrations()
        logger.info("Database ready.")

    async def close(self) -> None:
        """
        Close the database connection gracefully.

        Safe to call when not connected.
        """
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None
        logger.info("Database connection closed.")

    # ── Schema ─────────────────────────────────────────────────────────────

    async def _init_schema(self) -> None:
        """Create all tables and indexes if they do not already exist."""
        assert self._conn is not None

        logger.debug("Initialising database schema.")
        async with self._conn.cursor() as cursor:
            for ddl in _ALL_DDL:
                await cursor.execute(ddl)
            for index_ddl in _CREATE_INDEXES:
                await cursor.execute(index_ddl)

        await self._conn.commit()
        logger.debug("Schema initialised successfully.")

    async def _apply_migrations(self) -> None:
        """
        Apply additive column migrations then create any dependent indexes.

        Each ``ALTER TABLE … ADD COLUMN …`` is silently ignored when the
        column already exists (idempotent). Sprint indexes that reference
        new columns are created here, after the columns are guaranteed to
        exist.
        """
        assert self._conn is not None
        applied = 0

        all_migrations = _SPRINT2_MIGRATIONS + _SPRINT3_MIGRATIONS
        for table, column, col_def in all_migrations:
            try:
                await self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_def};"
                )
                await self._conn.commit()
                applied += 1
                logger.debug("Migration applied: %s.%s", table, column)
            except aiosqlite.OperationalError:
                # Column already exists — expected on subsequent starts.
                pass
        if applied:
            logger.info("Applied %d schema migration(s).", applied)

        # Create Sprint 2 + Sprint 3 indexes (safe now that columns exist).
        async with self._conn.cursor() as cursor:
            for idx_ddl in _SPRINT2_INDEXES + _SPRINT3_INDEXES:
                await cursor.execute(idx_ddl)
        await self._conn.commit()
        logger.debug("Sprint 2 + Sprint 3 indexes ensured.")

    # ── Connection access ──────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True when the database connection is open."""
        return self._conn is not None

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[aiosqlite.Cursor]:
        """
        Async context manager that yields a cursor on the open connection.

        Automatically commits on exit; rolls back on exception.

        Raises
        ------
        RuntimeError
            When the database is not connected.
        """
        if self._conn is None:
            raise RuntimeError(
                "Database is not connected. Call DatabaseManager.connect() first."
            )
        async with self._conn.cursor() as cur:
            try:
                yield cur
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    async def execute(self, sql: str, params: tuple = ()) -> tuple[int | None, int]:
        """
        Execute a single DML statement, commit, and return row metadata.

        Use :meth:`cursor` directly for multi-statement transactions or
        when you need fine-grained cursor control.

        Parameters
        ----------
        sql:
            The SQL statement to run (INSERT, UPDATE, DELETE, etc.).
        params:
            Optional positional parameters for the statement.

        Returns
        -------
        tuple[int | None, int]
            A ``(lastrowid, rowcount)`` pair captured *before* the cursor
            closes, so callers can inspect inserted row IDs reliably.
        """
        async with self.cursor() as cur:
            await cur.execute(sql, params)
            return (cur.lastrowid, cur.rowcount)

    async def fetchall(
        self, sql: str, params: tuple = ()
    ) -> list[aiosqlite.Row]:
        """
        Execute a SELECT and return all rows.

        Parameters
        ----------
        sql:
            The SELECT statement.
        params:
            Optional positional parameters.

        Returns
        -------
        list[aiosqlite.Row]
            All matching rows.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def fetchone(
        self, sql: str, params: tuple = ()
    ) -> Optional[aiosqlite.Row]:
        """
        Execute a SELECT and return the first row, or None.

        Parameters
        ----------
        sql:
            The SELECT statement.
        params:
            Optional positional parameters.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def health_check(self) -> dict[str, object]:
        """
        Return a health summary for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``connected`` (bool), ``path`` (str), ``table_count`` (int).
        """
        if not self.is_connected:
            return {"connected": False, "path": self.path, "table_count": 0}

        rows = await self.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table';"
        )
        return {
            "connected": True,
            "path": self.path,
            "table_count": len(rows),
        }

    # ── Sprint 4: Maintenance operations ──────────────────────────────────

    async def vacuum(self) -> None:
        """
        Run VACUUM to compact the SQLite file and reclaim free pages.

        Safe to call at any time. Commits any pending transaction first.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        logger.info("Database: running VACUUM.")
        await self._conn.execute("VACUUM;")
        await self._conn.commit()
        logger.info("Database: VACUUM complete.")

    async def integrity_check(self) -> str:
        """
        Run PRAGMA integrity_check and return a summary string.

        Returns
        -------
        str
            ``"ok"`` when the database is healthy, or a comma-separated list
            of up to five error descriptions when corruption is detected.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        rows = await self.fetchall("PRAGMA integrity_check;")
        results = [str(r[0]) for r in rows]
        if results == ["ok"]:
            return "ok"
        return "; ".join(results[:5])

    async def cleanup_old_records(self, retention_days: int = 7) -> dict[str, int]:
        """
        Delete records older than *retention_days* from high-volume tables.

        Tables cleaned:
        - ``history``     — time-series market snapshots
        - ``evaluations`` — per-token scoring results
        - ``logs``        — structured log sink

        Alert records are deliberately excluded; they are kept for audit.

        Parameters
        ----------
        retention_days:
            Records older than this many days are deleted.

        Returns
        -------
        dict[str, int]
            Mapping of ``{table_name: rows_deleted}`` for tables where at
            least one row was removed.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected.")

        stats: dict[str, int] = {}
        targets = [
            ("history",     "recorded_at"),
            ("evaluations", "evaluated_at"),
            ("logs",        "logged_at"),
        ]

        for table, col in targets:
            try:
                cursor = await self._conn.execute(
                    f"DELETE FROM {table} WHERE {col} < datetime('now', ?);"
                    , (f"-{retention_days} days",),
                )
                await self._conn.commit()
                if cursor.rowcount:
                    stats[table] = cursor.rowcount
                    logger.debug(
                        "Cleanup: deleted %d rows from %s.", cursor.rowcount, table
                    )
            except Exception as exc:
                logger.warning("Cleanup failed for %s: %s", table, exc)

        return stats

    async def backup(self, backup_dir: str = "backups") -> str | None:
        """
        Create a consistent backup of the SQLite database using the SQLite
        online-backup API.

        Using ``sqlite3.connect().backup()`` is safe on a WAL-mode database
        because it snapshots a consistent state even while writes are in
        flight.  The old ``shutil.copy2`` approach could produce a corrupt
        backup when WAL frames had not yet been folded back into the main file.

        Parameters
        ----------
        backup_dir:
            Directory where backup files are stored. Created if absent.

        Returns
        -------
        str | None
            The path of the created backup file, or ``None`` on failure.
        """
        import os
        import sqlite3

        from app.utils.time_utils import utcnow

        if not self.is_connected:
            logger.warning("Backup skipped — database not connected.")
            return None

        try:
            os.makedirs(backup_dir, exist_ok=True)
            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            backup_name = f"memecrawler_{ts}.db"
            backup_path = os.path.join(backup_dir, backup_name)

            # Checkpoint WAL into the main file before copying so the backup
            # contains all committed transactions.
            assert self._conn is not None
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

            # Use the sqlite3 backup API for a guaranteed-consistent snapshot.
            # This runs in a thread executor to avoid blocking the event loop.
            src_path = self.path

            def _do_backup() -> None:
                with sqlite3.connect(src_path) as src:
                    with sqlite3.connect(backup_path) as dst:
                        src.backup(dst)

            import asyncio
            await asyncio.get_event_loop().run_in_executor(None, _do_backup)

            logger.info("Database backed up to %s.", backup_path)
            return backup_path
        except Exception as exc:
            logger.error("Database backup failed: %s", exc)
            return None

    async def table_row_counts(self) -> dict[str, int]:
        """
        Return the row count for every user table in the database.

        Returns
        -------
        dict[str, int]
            Mapping of ``{table_name: row_count}``.
        """
        if not self.is_connected:
            return {}

        rows = await self.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        counts: dict[str, int] = {}
        for row in rows:
            table = row[0]
            try:
                count_row = await self.fetchone(
                    f"SELECT COUNT(*) AS cnt FROM {table};"
                )
                counts[table] = count_row["cnt"] if count_row else 0
            except Exception:
                counts[table] = -1
        return counts

    async def db_file_size_bytes(self) -> int:
        """Return the database file size in bytes, or 0 if unavailable."""
        import os
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0
