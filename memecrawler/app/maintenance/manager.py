"""
Maintenance Manager — Sprint 4.

Runs periodic housekeeping tasks to keep MemeCrawler healthy in
long-running production deployments:

- Cache eviction      : remove expired in-process cache entries.
- Record cleanup      : delete old high-volume rows respecting retention policy.
- VACUUM              : compact the SQLite file and reclaim free pages.
- Integrity check     : run PRAGMA integrity_check and log any corruption.
- Backup              : copy the SQLite file to a timestamped archive.

All tasks are non-blocking, wrapped in individual try/except blocks so that
one failure does not cancel the others. The scheduler registers two jobs:

    ``db_maintenance``  (hourly by default)   → run_maintenance()
    ``db_vacuum``       (daily by default)    → run_vacuum()
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.cache.manager import CacheManager
    from app.config.settings import Settings
    from app.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


class MaintenanceManager:
    """
    Orchestrates periodic DB and cache maintenance.

    Parameters
    ----------
    db:
        The application DatabaseManager instance.
    cache:
        The application CacheManager instance.
    settings:
        Application settings (reads maintenance_interval, db_retention_days,
        enable_db_backup, backup_dir).
    """

    def __init__(
        self,
        db: "DatabaseManager",
        cache: "CacheManager",
        settings: "Settings",
    ) -> None:
        self._db = db
        self._cache = cache
        self._settings = settings

        self._cleanups_run: int = 0
        self._vacuums_run: int = 0
        self._backups_run: int = 0
        self._total_cache_evictions: int = 0
        self._last_cleanup_at: Optional[str] = None
        self._last_vacuum_at: Optional[str] = None
        self._last_backup_at: Optional[str] = None
        self._last_integrity_status: str = "not checked"

    # ── Public scheduler callbacks ────────────────────────────────────────

    async def run_maintenance(self) -> None:
        """
        Routine maintenance cycle — called by the ``db_maintenance`` scheduler job.

        1. Evict expired cache entries.
        2. Delete old DB records according to the retention policy.
        """
        logger.info("Maintenance: starting routine cycle.")

        # 1. Cache eviction
        try:
            evicted = self._cache.evict_expired()
            self._total_cache_evictions += evicted
            if evicted:
                logger.info("Maintenance: evicted %d expired cache entries.", evicted)
            else:
                logger.debug("Maintenance: no expired cache entries to evict.")
        except Exception as exc:
            logger.warning("Maintenance: cache eviction failed: %s", exc)

        # 2. Old record cleanup
        try:
            stats = await self._db.cleanup_old_records(self._settings.db_retention_days)
            total_deleted = sum(stats.values())
            if total_deleted:
                logger.info(
                    "Maintenance: deleted %d old records across %d table(s): %s",
                    total_deleted,
                    len(stats),
                    stats,
                )
            else:
                logger.debug("Maintenance: no old records to clean up.")
        except Exception as exc:
            logger.warning("Maintenance: record cleanup failed: %s", exc)

        self._cleanups_run += 1
        self._last_cleanup_at = utcnow_iso()
        logger.info("Maintenance: routine cycle complete.")

    async def run_vacuum(self) -> None:
        """
        VACUUM + integrity check — called by the ``db_vacuum`` scheduler job.

        Also triggers a backup if ``enable_db_backup`` is True.
        """
        logger.info("Maintenance: starting VACUUM cycle.")

        # VACUUM
        try:
            await self._db.vacuum()
            self._vacuums_run += 1
            self._last_vacuum_at = utcnow_iso()
            logger.info("Maintenance: VACUUM complete.")
        except Exception as exc:
            logger.warning("Maintenance: VACUUM failed: %s", exc)

        # Integrity check
        try:
            status = await self._db.integrity_check()
            self._last_integrity_status = status
            if status == "ok":
                logger.info("Maintenance: DB integrity check passed.")
            else:
                logger.error(
                    "Maintenance: DB integrity check FAILED — %s", status
                )
        except Exception as exc:
            logger.warning("Maintenance: integrity check error: %s", exc)
            self._last_integrity_status = f"error: {exc}"

        # Optional backup
        if self._settings.enable_db_backup:
            await self.run_backup()

    async def run_backup(self) -> None:
        """Copy the SQLite file to a timestamped archive."""
        if not self._settings.enable_db_backup:
            logger.debug("Maintenance: backups disabled — skipping.")
            return

        try:
            backup_path = await self._db.backup(self._settings.backup_dir)
            if backup_path:
                self._backups_run += 1
                self._last_backup_at = utcnow_iso()
                logger.info("Maintenance: DB backup created at %s.", backup_path)
            else:
                logger.warning("Maintenance: DB backup returned no path.")
        except Exception as exc:
            logger.warning("Maintenance: DB backup failed: %s", exc)

    # ── Status ────────────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """
        Return a status summary for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``cleanups_run``, ``vacuums_run``, ``backups_run``,
            ``total_cache_evictions``, ``last_cleanup_at``,
            ``last_vacuum_at``, ``last_backup_at``,
            ``last_integrity_status``.
        """
        return {
            "cleanups_run": self._cleanups_run,
            "vacuums_run": self._vacuums_run,
            "backups_run": self._backups_run,
            "total_cache_evictions": self._total_cache_evictions,
            "last_cleanup_at": self._last_cleanup_at,
            "last_vacuum_at": self._last_vacuum_at,
            "last_backup_at": self._last_backup_at,
            "last_integrity_status": self._last_integrity_status,
        }
