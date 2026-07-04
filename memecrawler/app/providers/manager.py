"""
Provider manager.

Owns the collection of registered providers and exposes:

- Registration of provider instances.
- Health-checking all active providers.
- Retrieval of a provider by name.

Sprint 2 will add provider-dispatch logic (routing a token lookup to the
appropriate provider based on feature flags and availability).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.providers.base import BaseProvider, ProviderStatus
from app.utils.errors import ProviderNotFoundError

logger = logging.getLogger(__name__)


class ProviderManager:
    """
    Registry and lifecycle manager for all data providers.

    Usage
    -----
    ::

        manager = ProviderManager()
        manager.register(DexScreenerProvider(http_client))
        await manager.check_all()
        provider = manager.get("dexscreener")
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}

    # ── Registration ────────────────────────────────────────────────────────

    def register(self, provider: BaseProvider) -> None:
        """
        Add a provider to the registry.

        Parameters
        ----------
        provider:
            A concrete :class:`~app.providers.base.BaseProvider` instance.

        Raises
        ------
        ValueError
            When a provider with the same name is already registered.
        """
        if provider.name in self._providers:
            raise ValueError(
                f"Provider '{provider.name}' is already registered. "
                "Use a unique name or deregister the existing one first."
            )
        self._providers[provider.name] = provider
        logger.info("Provider registered: %s", provider.name)

    def deregister(self, name: str) -> None:
        """
        Remove a provider from the registry.

        Parameters
        ----------
        name:
            The provider name to remove.
        """
        if name in self._providers:
            del self._providers[name]
            logger.info("Provider deregistered: %s", name)

    # ── Retrieval ───────────────────────────────────────────────────────────

    def get(self, name: str) -> BaseProvider:
        """
        Return a provider by name.

        Parameters
        ----------
        name:
            The registered provider name.

        Raises
        ------
        ProviderNotFoundError
            When no provider with the given name is registered.
        """
        if name not in self._providers:
            raise ProviderNotFoundError(
                f"Provider '{name}' is not registered.",
                code="PROVIDER_NOT_FOUND",
            )
        return self._providers[name]

    def all(self) -> list[BaseProvider]:
        """Return all registered providers."""
        return list(self._providers.values())

    @property
    def names(self) -> list[str]:
        """Return the names of all registered providers."""
        return list(self._providers.keys())

    # ── Health ──────────────────────────────────────────────────────────────

    async def check_all(self) -> dict[str, ProviderStatus]:
        """
        Run health checks on all registered providers concurrently.

        Returns
        -------
        dict[str, ProviderStatus]
            Mapping of provider name → status after checking.
        """
        if not self._providers:
            logger.debug("No providers registered — skipping health check.")
            return {}

        logger.info(
            "Running health checks for %d provider(s).", len(self._providers)
        )
        tasks = {
            name: provider.health_check()
            for name, provider in self._providers.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        status_map: dict[str, ProviderStatus] = {}

        for name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(
                    "Health check for provider '%s' raised an exception: %s",
                    name,
                    result,
                )
                status_map[name] = ProviderStatus.DOWN
            else:
                status_map[name] = result  # type: ignore[assignment]

        return status_map

    # ── Status summary ──────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """
        Return a summary dict for the /health API endpoint.

        Returns
        -------
        dict
            Keys: ``registered`` (int), ``healthy`` (int), ``providers`` (list).
        """
        provider_infos = [p.info() for p in self._providers.values()]
        healthy = sum(
            1 for p in self._providers.values()
            if p.status is ProviderStatus.HEALTHY
        )
        return {
            "registered": len(self._providers),
            "healthy": healthy,
            "providers": provider_infos,
        }
