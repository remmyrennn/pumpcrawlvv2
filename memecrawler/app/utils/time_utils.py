"""
Time and datetime utilities.

All datetimes are UTC-aware. No module should use ``datetime.now()``
without timezone information; use :func:`utcnow` instead.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC-aware datetime."""
    return datetime.now(tz=timezone.utc)


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return utcnow().isoformat()


def utcnow_ts() -> float:
    """Return the current UTC time as a POSIX timestamp (float)."""
    return time.time()


def from_timestamp(ts: float) -> datetime:
    """Convert a POSIX timestamp to a UTC-aware datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def age_seconds(created_at: datetime) -> float:
    """
    Return the number of seconds elapsed since ``created_at``.

    Parameters
    ----------
    created_at:
        A UTC-aware datetime representing the creation time.
    """
    return (utcnow() - created_at).total_seconds()


def seconds_until(target: datetime) -> float:
    """
    Return the number of seconds until ``target``.

    Returns a negative value if ``target`` is in the past.
    """
    return (target - utcnow()).total_seconds()


def format_duration(seconds: float) -> str:
    """
    Format a duration given in seconds as a human-readable string.

    Examples
    --------
    >>> format_duration(90)
    '1m 30s'
    >>> format_duration(3700)
    '1h 1m 40s'
    """
    seconds = int(abs(seconds))
    hours, remainder = divmod(seconds, 3_600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def format_uptime(start_time: float) -> str:
    """
    Return a human-readable uptime string given a POSIX start timestamp.

    Parameters
    ----------
    start_time:
        POSIX timestamp of when the process started (from :func:`utcnow_ts`).
    """
    elapsed = utcnow_ts() - start_time
    return format_duration(elapsed)
