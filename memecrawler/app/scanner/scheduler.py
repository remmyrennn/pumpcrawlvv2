"""
Scanner scheduler framework.

Provides the recurring task scheduler that will drive token scans in
Sprint 2. Sprint 1 establishes the complete scheduling architecture:
job registration, start/stop lifecycle, and error isolation between jobs.

No business logic is implemented here. Scanning logic is added in Sprint 2.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────────

AsyncJob = Callable[[], Coroutine[Any, Any, None]]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class JobDescriptor:
    """
    Metadata for a single scheduled job.

    Attributes
    ----------
    name:
        Human-readable job identifier (used in logs).
    func:
        The async callable to invoke on each tick.
    interval_seconds:
        How often (in seconds) the job runs.
    run_immediately:
        When True, the job runs once at startup before the first sleep.
    enabled:
        When False, the job is registered but never executed.
    last_run_at:
        ISO 8601 timestamp of the most recent successful execution.
    error_count:
        Number of consecutive errors since the last successful run.
    """

    name: str
    func: AsyncJob
    interval_seconds: int
    run_immediately: bool = True
    enabled: bool = True
    last_run_at: str | None = None
    error_count: int = 0


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    """
    Async task scheduler with independent per-job error isolation.

    Each registered job runs in its own asyncio task. An unhandled exception
    in one job does NOT terminate other jobs — it is caught, logged, and the
    job continues on its next interval.

    Usage
    -----
    ::

        scheduler = Scheduler()
        scheduler.register(
            name="token_scan",
            func=my_scan_coroutine,
            interval_seconds=300,
        )
        await scheduler.start()
        # ... application runs ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobDescriptor] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: bool = False

    # ── Registration ────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        func: AsyncJob,
        interval_seconds: int,
        *,
        run_immediately: bool = True,
        enabled: bool = True,
    ) -> None:
        """
        Register an async job with the scheduler.

        Parameters
        ----------
        name:
            Unique identifier for the job.
        func:
            The async callable invoked on each tick.
        interval_seconds:
            Number of seconds between executions.
        run_immediately:
            Run the job once before the first sleep interval.
        enabled:
            Whether the job should execute. Disabled jobs are registered
            but silently skipped at runtime.

        Raises
        ------
        ValueError
            When a job with the same name is already registered.
        """
        if name in self._jobs:
            raise ValueError(
                f"Job '{name}' is already registered. "
                "Deregister it first or choose a unique name."
            )
        self._jobs[name] = JobDescriptor(
            name=name,
            func=func,
            interval_seconds=interval_seconds,
            run_immediately=run_immediately,
            enabled=enabled,
        )
        logger.info(
            "Job registered: '%s' (interval=%ds, enabled=%s).",
            name,
            interval_seconds,
            enabled,
        )

    def deregister(self, name: str) -> None:
        """
        Remove a job from the registry.

        If the job is currently running, it will be cancelled.

        Parameters
        ----------
        name:
            The job name to remove.
        """
        if name in self._tasks:
            self._tasks[name].cancel()
            del self._tasks[name]
        self._jobs.pop(name, None)
        logger.info("Job deregistered: '%s'.", name)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start all registered, enabled jobs as independent asyncio tasks.

        Safe to call when already running (no-op).
        """
        if self._running:
            logger.debug("Scheduler already running — skipping start.")
            return

        self._running = True
        enabled_jobs = [j for j in self._jobs.values() if j.enabled]
        logger.info(
            "Starting scheduler with %d/%d enabled job(s).",
            len(enabled_jobs),
            len(self._jobs),
        )
        for job in enabled_jobs:
            self._tasks[job.name] = asyncio.create_task(
                self._run_job_loop(job),
                name=f"scheduler:{job.name}",
            )

    async def stop(self) -> None:
        """
        Cancel all running job tasks and await their completion.

        Safe to call when already stopped (no-op).
        """
        if not self._running:
            return

        self._running = False
        logger.info("Stopping scheduler — cancelling %d task(s).", len(self._tasks))

        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Job task '%s' cancelled.", name)

        self._tasks.clear()
        logger.info("Scheduler stopped.")

    # ── Job loop ─────────────────────────────────────────────────────────────

    async def _run_job_loop(self, job: JobDescriptor) -> None:
        """
        Internal loop for a single job.

        Runs until cancelled. Catches all exceptions to prevent one
        misbehaving job from crashing the scheduler.

        Parameters
        ----------
        job:
            The job descriptor to execute repeatedly.
        """
        logger.info("Job loop started: '%s'.", job.name)

        if not job.run_immediately:
            await asyncio.sleep(job.interval_seconds)

        while True:
            try:
                await job.func()
                job.last_run_at = utcnow_iso()
                job.error_count = 0
                logger.debug("Job '%s' completed successfully.", job.name)
            except asyncio.CancelledError:
                logger.info("Job '%s' received cancellation signal.", job.name)
                raise
            except Exception as exc:
                job.error_count += 1
                logger.exception(
                    "Job '%s' raised an unhandled exception (error #%d): %s",
                    job.name,
                    job.error_count,
                    exc,
                )
            await asyncio.sleep(job.interval_seconds)

    # ── Status ──────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True when the scheduler has been started and not yet stopped."""
        return self._running

    def info(self) -> dict[str, Any]:
        """
        Return a status summary for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``running``, ``job_count``, ``jobs``.
        """
        jobs_info = [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "enabled": j.enabled,
                "last_run_at": j.last_run_at,
                "error_count": j.error_count,
            }
            for j in self._jobs.values()
        ]
        return {
            "running": self._running,
            "job_count": len(self._jobs),
            "jobs": jobs_info,
        }
