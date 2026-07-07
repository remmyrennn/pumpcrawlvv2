"""
Helius provider.

Provides enhanced Solana RPC data including token metadata, holder counts,
transaction history, and DAS (Digital Asset Standard) API access via Helius.

Sprint 1: provider framework and health check only.
Sprint 2: implement holder count fetch, token metadata, and webhook support.

Requires: ``HELIUS_API_KEY`` and ``ENABLE_HELIUS=true`` in .env.
"""

from __future__ import annotations

import logging

import httpx

from app.providers.base import BaseProvider, ProviderStatus
from app.utils.errors import ConfigurationError

logger = logging.getLogger(__name__)

_BASE_URL = "https://mainnet.helius-rpc.com"


class HeliusProvider(BaseProvider):
    """
    Provider for Helius enhanced Solana RPC.

    Parameters
    ----------
    http_client:
        Shared async HTTP client.
    api_key:
        Helius API key from the Helius dashboard.

    Attributes
    ----------
    name:
        Registry key used by :class:`~app.providers.manager.ProviderManager`.
    """

    name = "helius"
    version = "1.0.0"

    def __init__(self, http_client: httpx.AsyncClient, api_key: str) -> None:
        super().__init__(http_client)
        if not api_key.strip():
            raise ConfigurationError(
                "HeliusProvider requires a valid HELIUS_API_KEY.",
                code="MISSING_HELIUS_KEY",
            )
        self._api_key = api_key
        self._rpc_url = f"{_BASE_URL}/?api-key={self._api_key}"

    async def health_check(self) -> ProviderStatus:
        """
        Verify connectivity to the Helius RPC endpoint.

        Sends a ``getHealth`` JSON-RPC call and checks for a valid response.

        Returns
        -------
        ProviderStatus
            HEALTHY on success, DEGRADED/DOWN on error.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getHealth",
        }
        try:
            response = await self._client.post(
                self._rpc_url, json=payload, timeout=10.0
            )
            data = response.json()
            if response.status_code == 200 and data.get("result") == "ok":
                self._record_success()
                logger.debug("Helius health check passed.")
            else:
                raise ValueError(
                    f"Unexpected response: {response.status_code} {data}"
                )
        except Exception as exc:
            self._record_error(exc)
            logger.warning("Helius health check failed: %s", exc)

        return self._status
