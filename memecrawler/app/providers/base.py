"""
Abstract base class for all data providers.

Every external data source (DexScreener, Pump.fun, Helius, RugCheck, RPC)
must implement :class:`BaseProvider`. The :class:`ProviderManager` depends
only on this interface, ensuring that providers are interchangeable and
testable in isolation.

Sprint 2 adds: latency tracking, last_success_at, last_failure_at.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Status enum ───────────────────────────────────────────────────────────────

class ProviderStatus(str, Enum):
    """Represents the current operational status of a provider."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


# ── Base provider ─────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    """
    Abstract interface for a MemeCrawler data provider.

    Subclasses must implement :meth:`health_check` and whatever data-fetch
    methods are appropriate for the provider.

    Parameters
    ----------
    http_client:
        The shared async HTTP client (from ``app.http_client``).
    """

    #: Human-readable name used in logs and the /health endpoint.
    name: str = "unnamed"

    #: Version string for this provider implementation.
    version: str = "1.0.0"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._status: ProviderStatus = ProviderStatus.UNKNOWN
        self._error_count: int = 0
        self._last_success_at: Optional[str] = None
        self._last_failure_at: Optional[str] = None
        self._latency_ms: Optional[float] = None   # most recent request latency
        self._total_requests: int = 0
        self._logger = logging.getLogger(f"{__name__}.{self.name}")

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def status(self) -> ProviderStatus:
        """Current operational status of the provider."""
        return self._status

    @property
    def error_count(self) -> int:
        """Number of consecutive errors since the last successful request."""
        return self._error_count

    @property
    def is_healthy(self) -> bool:
        """True when the provider is reporting HEALTHY status."""
        return self._status is ProviderStatus.HEALTHY

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    async def health_check(self) -> ProviderStatus:
        """
        Probe the external service and return the provider's current status.

        Implementations must update ``self._status`` before returning and
        must not raise exceptions — all errors must be caught internally and
        reflected in the returned status.

        Returns
        -------
        ProviderStatus
            The new status after probing.
        """

    # ── Shared helpers ─────────────────────────────────────────────────────

    def _record_success(self, latency_ms: Optional[float] = None) -> None:
        """Mark the provider as healthy, reset the error counter, and record latency."""
        from app.utils.time_utils import utcnow_iso
        self._status = ProviderStatus.HEALTHY
        self._error_count = 0
        self._last_success_at = utcnow_iso()
        self._total_requests += 1
        if latency_ms is not None:
            self._latency_ms = latency_ms

    def _record_error(self, exc: Exception) -> None:
        """
        Increment the error counter and update the status accordingly.

        Transitions to DEGRADED after the first error, DOWN after three.

        Parameters
        ----------
        exc:
            The exception that triggered the error.
        """
        from app.utils.time_utils import utcnow_iso
        self._error_count += 1
        self._last_failure_at = utcnow_iso()
        self._total_requests += 1
        self._logger.warning(
            "Provider '%s' error #%d: %s", self.name, self._error_count, exc
        )
        if self._error_count >= 3:
            self._status = ProviderStatus.DOWN
        else:
            self._status = ProviderStatus.DEGRADED

    def _timed_get(self, url: str, **kwargs: Any):
        """
        Convenience context manager placeholder — callers measure latency themselves.

        Providers should measure round-trip time and pass it to ``_record_success``.
        """

    # ── Info ───────────────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """
        Return a summary dict for use in the /health and /diagnostics endpoints.

        Returns
        -------
        dict
            Keys: ``name``, ``version``, ``status``, ``error_count``,
            ``last_success_at``, ``last_failure_at``, ``latency_ms``,
            ``total_requests``.
        """
        return {
            "name": self.name,
            "version": self.version,
            "status": self._status.value,
            "error_count": self._error_count,
            "last_success_at": self._last_success_at,
            "last_failure_at": self._last_failure_at,
            "latency_ms": round(self._latency_ms, 1) if self._latency_ms is not None else None,
            "total_requests": self._total_requests,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} status={self._status.value!r}>"
