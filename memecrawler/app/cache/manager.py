"""
Runtime cache manager.

Provides a simple in-process key-value cache with optional TTL (time-to-live)
expiry. No external services (Redis, Memcached) are required in Sprint 1.

Sprint 4 may replace or supplement this with a Supabase-backed cache for
cross-process sharing, but this module's interface will remain stable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.utils.errors import CacheError

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

NO_EXPIRY: float = -1.0


# ── Entry ─────────────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    """Internal representation of a single cached item."""

    value: Any
    expires_at: float  # POSIX timestamp; NO_EXPIRY (-1) means never expires


# ── Manager ───────────────────────────────────────────────────────────────────

class CacheManager:
    """
    Thread-safe (within asyncio) in-process key-value cache.

    All keys are strings. Values can be any Python object.

    Usage
    -----
    ::

        cache = CacheManager()
        cache.set("token:So111...112", price_data, ttl=60)
        price = cache.get("token:So111...112")
        cache.delete("token:So111...112")
    """

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}

    # ── CRUD ────────────────────────────────────────────────────────────────

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: Optional[float] = None,
    ) -> None:
        """
        Store a value under ``key``.

        Parameters
        ----------
        key:
            Cache key (non-empty string).
        value:
            The value to cache.
        ttl:
            Time-to-live in seconds. When None or not provided, the entry
            never expires. Pass ``0`` to store without expiry.

        Raises
        ------
        CacheError
            When ``key`` is empty.
        """
        if not key:
            raise CacheError("Cache key must be a non-empty string.", code="EMPTY_KEY")

        if ttl is not None and ttl > 0:
            expires_at = time.monotonic() + ttl
        else:
            expires_at = NO_EXPIRY

        self._store[key] = _CacheEntry(value=value, expires_at=expires_at)
        logger.debug("Cache SET '%s' (ttl=%s).", key, ttl)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a value from the cache.

        Parameters
        ----------
        key:
            Cache key to look up.
        default:
            Value returned when the key does not exist or has expired.

        Returns
        -------
        Any
            The cached value, or ``default``.
        """
        entry = self._store.get(key)
        if entry is None:
            return default
        if entry.expires_at != NO_EXPIRY and time.monotonic() > entry.expires_at:
            logger.debug("Cache MISS (expired) '%s'.", key)
            del self._store[key]
            return default
        logger.debug("Cache HIT '%s'.", key)
        return entry.value

    def delete(self, key: str) -> bool:
        """
        Remove a key from the cache.

        Parameters
        ----------
        key:
            The key to remove.

        Returns
        -------
        bool
            True when the key existed and was deleted; False otherwise.
        """
        existed = key in self._store
        self._store.pop(key, None)
        if existed:
            logger.debug("Cache DELETE '%s'.", key)
        return existed

    def has(self, key: str) -> bool:
        """
        Return True when a valid (non-expired) entry exists for ``key``.

        Parameters
        ----------
        key:
            The key to check.
        """
        return self.get(key, _SENTINEL) is not _SENTINEL

    def clear(self) -> int:
        """
        Remove all entries from the cache.

        Returns
        -------
        int
            Number of entries that were removed.
        """
        count = len(self._store)
        self._store.clear()
        logger.info("Cache cleared (%d entries removed).", count)
        return count

    def evict_expired(self) -> int:
        """
        Remove all expired entries from the cache.

        Useful for periodic cleanup to prevent unbounded memory growth.

        Returns
        -------
        int
            Number of expired entries removed.
        """
        now = time.monotonic()
        expired_keys = [
            k
            for k, v in self._store.items()
            if v.expires_at != NO_EXPIRY and now > v.expires_at
        ]
        for key in expired_keys:
            del self._store[key]
        if expired_keys:
            logger.debug("Evicted %d expired cache entries.", len(expired_keys))
        return len(expired_keys)

    # ── Namespace helpers ────────────────────────────────────────────────────

    def set_token(self, mint: str, data: Any, *, ttl: float = 300.0) -> None:
        """Shorthand for caching token data keyed by mint address."""
        self.set(f"token:{mint}", data, ttl=ttl)

    def get_token(self, mint: str) -> Any:
        """Shorthand for retrieving cached token data by mint address."""
        return self.get(f"token:{mint}")

    def invalidate_token(self, mint: str) -> bool:
        """Shorthand for removing cached token data by mint address."""
        return self.delete(f"token:{mint}")

    # ── Stats ────────────────────────────────────────────────────────────────

    def info(self) -> dict[str, int]:
        """
        Return cache statistics for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``total_entries``, ``expired_entries``.
        """
        now = time.monotonic()
        expired = sum(
            1
            for v in self._store.values()
            if v.expires_at != NO_EXPIRY and now > v.expires_at
        )
        return {
            "total_entries": len(self._store),
            "expired_entries": expired,
        }


# ── Module-level sentinel ─────────────────────────────────────────────────────

_SENTINEL = object()
