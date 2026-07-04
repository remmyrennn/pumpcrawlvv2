"""
Retry helpers built on top of the ``tenacity`` library.

Provides pre-configured retry strategies for HTTP calls and database
operations so that callers do not need to configure tenacity directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_MAX_ATTEMPTS: int = 3
DEFAULT_WAIT_MIN: float = 1.0   # seconds
DEFAULT_WAIT_MAX: float = 10.0  # seconds
DEFAULT_WAIT_MULTIPLIER: float = 2.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log_retry_attempt(retry_state: Any) -> None:
    """Log each retry attempt at WARNING level."""
    attempt = retry_state.attempt_number
    exc = retry_state.outcome.exception()
    logger.warning(
        "Retry attempt %d/%d after exception: %s",
        attempt,
        retry_state.retry_object.stop.max_attempt_number,
        exc,
    )


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    wait_min: float = DEFAULT_WAIT_MIN,
    wait_max: float = DEFAULT_WAIT_MAX,
    multiplier: float = DEFAULT_WAIT_MULTIPLIER,
    reraise: bool = True,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """
    Execute an async callable with exponential-backoff retry logic.

    Parameters
    ----------
    func:
        The async callable to invoke.
    *args:
        Positional arguments forwarded to ``func``.
    max_attempts:
        Maximum number of attempts before giving up.
    wait_min:
        Minimum wait time between attempts (seconds).
    wait_max:
        Maximum wait time between attempts (seconds).
    multiplier:
        Exponential backoff multiplier.
    reraise:
        If True, re-raise the last exception after all attempts are
        exhausted. If False, return None.
    retry_exceptions:
        Tuple of exception types that trigger a retry. Other exceptions
        propagate immediately.
    **kwargs:
        Keyword arguments forwarded to ``func``.

    Returns
    -------
    Any
        The return value of ``func`` on success.

    Raises
    ------
    Exception
        The last exception raised by ``func`` when all attempts fail and
        ``reraise`` is True.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=wait_min, max=wait_max),
        retry=retry_if_exception_type(retry_exceptions),
        after=_log_retry_attempt,
        reraise=reraise,
    ):
        with attempt:
            return await func(*args, **kwargs)
