"""
MemeCrawler entry point.

Starts the uvicorn server with the FastAPI application defined in
``app.main``.

Usage
-----
::

    python run.py

Environment
-----------
PORT:
    The port to bind to (default: 8000). Set by the Replit workflow.
HOST:
    The bind address (default: 0.0.0.0).
LOG_LEVEL:
    Uvicorn log level (default: info). Overridden by app-level logging.
RELOAD:
    Set to "true" to enable hot-reload (development only).
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Configure and start the uvicorn server."""
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")  # noqa: S104
    reload = os.environ.get("RELOAD", "false").lower() == "true"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="warning",   # Application logger handles INFO; uvicorn stays quiet.
        reload=reload,
        access_log=False,      # Suppress per-request access logs in production.
    )


if __name__ == "__main__":
    main()
