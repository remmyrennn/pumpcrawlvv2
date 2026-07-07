"""
Shared async HTTP client.

A single ``httpx.AsyncClient`` is created at startup and shared across the
entire application. No module may instantiate its own client.

Usage
-----
::

    from app.http_client import get_http_client

    client = get_http_client()
    response = await client.get("https://api.example.com/data")

Lifecycle
---------
Call :func:`create_http_client` during startup and :func:`close_http_client`
during shutdown. Both are idempotent.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS: float = 30.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 10.0
MAX_CONNECTIONS: int = 100
MAX_KEEPALIVE_CONNECTIONS: int = 20
KEEPALIVE_EXPIRY_SECONDS: float = 30.0

USER_AGENT: str = "MemeCrawler/1.0 (+https://github.com/memecrawler)"

# ── Module-level singleton ────────────────────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def create_http_client() -> httpx.AsyncClient:
    """
    Initialise and store the shared async HTTP client.

    Returns the existing client if already created (idempotent).

    Returns
    -------
    httpx.AsyncClient
        The shared client instance.
    """
    global _client  # noqa: PLW0603

    if _client is not None:
        logger.debug("HTTP client already initialised — skipping.")
        return _client

    limits = httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
    )
    timeout = httpx.Timeout(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )
    _client = httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    logger.info(
        "HTTP client initialised (max_connections=%d, timeout=%.1fs).",
        MAX_CONNECTIONS,
        DEFAULT_TIMEOUT_SECONDS,
    )
    return _client


async def close_http_client() -> None:
    """
    Gracefully close the shared async HTTP client.

    Safe to call even when the client has not been initialised.
    """
    global _client  # noqa: PLW0603

    if _client is None:
        return

    await _client.aclose()
    _client = None
    logger.info("HTTP client closed.")


def get_http_client() -> httpx.AsyncClient:
    """
    Return the shared async HTTP client.

    Raises
    ------
    RuntimeError
        When :func:`create_http_client` has not been called yet.
    """
    if _client is None:
        raise RuntimeError(
            "HTTP client has not been initialised. "
            "Call create_http_client() during application startup."
        )
    return _client
