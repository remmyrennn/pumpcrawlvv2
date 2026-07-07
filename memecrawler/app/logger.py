"""
Centralised logging configuration.

Every module in MemeCrawler must obtain its logger via::

    import logging
    logger = logging.getLogger(__name__)

Call :func:`setup_logging` exactly once during startup (from ``app/main.py``)
before any other module emits log records.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# ── Formatting ────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_FORMATTER = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)


# ── Public API ────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """
    Configure the root logger for the entire application.

    This function must be called **once** at process startup, before any
    other module has a chance to emit log records. Subsequent calls are
    safe but have no effect (the root logger is only configured once).

    Parameters
    ----------
    level:
        The minimum log level as a string: DEBUG, INFO, WARNING, ERROR,
        or CRITICAL. Case-insensitive.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        # Already configured — avoid adding duplicate handlers.
        return

    root_logger.setLevel(numeric_level)

    # ── Console handler ───────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(_FORMATTER)
    root_logger.addHandler(console_handler)

    # Silence overly verbose third-party libraries at WARNING unless we
    # are in DEBUG mode ourselves.
    if numeric_level > logging.DEBUG:
        for noisy_lib in ("httpx", "httpcore", "telegram", "uvicorn.access"):
            logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        "Logging initialised at level %s", level.upper()
    )


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Convenience wrapper identical to ``logging.getLogger(name)``.
    Prefer using ``logging.getLogger(__name__)`` directly in each module.
    """
    return logging.getLogger(name)
