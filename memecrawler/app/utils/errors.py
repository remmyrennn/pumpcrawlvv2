"""
Custom exception hierarchy for MemeCrawler.

Every domain error should subclass ``MemeCrawlerError`` so callers can
catch all application exceptions with a single ``except`` clause.
"""

from __future__ import annotations


class MemeCrawlerError(Exception):
    """Base class for all MemeCrawler application errors."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


# ── Configuration ─────────────────────────────────────────────────────────────

class ConfigurationError(MemeCrawlerError):
    """Raised when required configuration is missing or invalid."""


# ── Database ──────────────────────────────────────────────────────────────────

class DatabaseError(MemeCrawlerError):
    """Raised when a database operation fails."""


class DatabaseConnectionError(DatabaseError):
    """Raised when the database cannot be reached."""


# ── HTTP / Network ────────────────────────────────────────────────────────────

class HttpClientError(MemeCrawlerError):
    """Raised when a shared HTTP client operation fails."""


class ProviderError(MemeCrawlerError):
    """Raised when a data provider fails to respond correctly."""


class ProviderNotFoundError(ProviderError):
    """Raised when a requested provider has not been registered."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider is temporarily unavailable."""


# ── Telegram ──────────────────────────────────────────────────────────────────

class TelegramError(MemeCrawlerError):
    """Raised when the Telegram bot encounters an error."""


class TelegramNotConfiguredError(TelegramError):
    """Raised when the bot token has not been supplied."""


class UnauthorisedUserError(TelegramError):
    """Raised when a non-authorised user attempts to interact with the bot."""


# ── Scanner / Discovery ───────────────────────────────────────────────────────

class ScannerError(MemeCrawlerError):
    """Raised when the scanner encounters an unrecoverable error."""


class DiscoveryError(MemeCrawlerError):
    """Raised when the discovery engine encounters an error."""


class WatchlistError(MemeCrawlerError):
    """Raised when a watchlist operation fails."""


class InvalidStateTransitionError(WatchlistError):
    """Raised when an invalid token state transition is attempted."""


# ── Cache ─────────────────────────────────────────────────────────────────────

class CacheError(MemeCrawlerError):
    """Raised when a cache operation fails."""
