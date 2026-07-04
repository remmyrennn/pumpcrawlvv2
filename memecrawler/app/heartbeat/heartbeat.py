"""
Heartbeat architecture.

Provides a scheduled heartbeat that periodically signals bot liveness.

Sprint 1: architecture only — the heartbeat is registered with the
scheduler but the actual message sending is disabled until Sprint 2
completes the Telegram notification flow.

The module is intentionally simple: it defines the heartbeat coroutine
and an integration helper that wires it into the :class:`~app.scanner.scheduler.Scheduler`.
"""

from __future__ import annotations

import logging

from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

HEARTBEAT_JOB_NAME = "heartbeat"


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class Heartbeat:
    """
    Manages the periodic liveness heartbeat.

    The heartbeat logs a message on each tick. In Sprint 2, it will also
    send a Telegram message to ``target_chat`` with a system status summary.

    Parameters
    ----------
    interval_seconds:
        How often (in seconds) the heartbeat fires.
    enabled:
        When False, the heartbeat is registered but silently skipped.
    """

    def __init__(
        self,
        interval_seconds: int,
        enabled: bool = True,
    ) -> None:
        self._interval = interval_seconds
        self._enabled = enabled
        self._tick_count: int = 0

    async def tick(self) -> None:
        """
        Execute one heartbeat tick.

        Called by the scheduler on each interval. Increments the tick
        counter and emits a log line. Sprint 2 will extend this to send
        a Telegram message.
        """
        self._tick_count += 1
        logger.info(
            "Heartbeat tick #%d at %s (Sprint 2 will send Telegram message).",
            self._tick_count,
            utcnow_iso(),
        )

    def register_with_scheduler(self, scheduler: object) -> None:
        """
        Register the heartbeat tick with the scheduler.

        Parameters
        ----------
        scheduler:
            A :class:`~app.scanner.scheduler.Scheduler` instance.

        Notes
        -----
        Uses duck typing to avoid a circular import between
        ``heartbeat`` and ``scanner``.
        """
        scheduler.register(  # type: ignore[attr-defined]
            name=HEARTBEAT_JOB_NAME,
            func=self.tick,
            interval_seconds=self._interval,
            run_immediately=False,
            enabled=self._enabled,
        )
        logger.info(
            "Heartbeat registered (interval=%ds, enabled=%s).",
            self._interval,
            self._enabled,
        )

    # ── Status ────────────────────────────────────────────────────────────────

    def info(self) -> dict[str, object]:
        """
        Return a status summary for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``enabled``, ``interval_seconds``, ``tick_count``.
        """
        return {
            "enabled": self._enabled,
            "interval_seconds": self._interval,
            "tick_count": self._tick_count,
        }
