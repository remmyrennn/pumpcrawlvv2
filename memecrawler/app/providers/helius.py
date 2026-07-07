"""
Helius provider.

Provides enhanced Solana RPC data including token metadata, holder counts,
transaction history, and DAS (Digital Asset Standard) API access via Helius.

Sprint 1: provider framework and health check only.
Sprint 2: implement holder count fetch, token metadata, and webhook support.
Sprint 5: get_new_tokens for discovery, get_token_metadata for scan fallback.

Requires: ``HELIUS_API_KEY`` set (env or hardcoded in settings.py).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.models.token import TokenData
from app.providers.base import BaseProvider, ProviderStatus
from app.utils.errors import ConfigurationError

logger = logging.getLogger(__name__)

_BASE_URL = "https://mainnet.helius-rpc.com"
_DAS_URL = "https://api.helius.xyz/v0"


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

    async def get_token_metadata(self, mint: str) -> Optional[TokenData]:
        """
        Fetch basic token metadata for a single mint using the Helius DAS API.

        Used as a fallback in the token scanner when DexScreener has no data
        for a very new or very low-volume token.

        Parameters
        ----------
        mint:
            The Solana token mint address.

        Returns
        -------
        TokenData | None
            Populated TokenData on success; None if not found or error.
        """
        url = f"{_DAS_URL}/token-metadata"
        params = {"api-key": self._api_key}
        payload = {"mintAccounts": [mint], "includeOffChain": False, "disableCache": False}
        try:
            response = await self._client.post(url, params=params, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.debug("Helius get_token_metadata HTTP %d for %s", response.status_code, mint[:12])
                return None
            data = response.json()
            items = data if isinstance(data, list) else []
            if not items:
                return None
            item = items[0]
            on_chain = item.get("onChainMetadata", {}) or {}
            meta = on_chain.get("metadata", {}) or {}
            account_info = on_chain.get("onChainAccountInfo", {}) or {}
            token_info = account_info.get("accountInfo", {}).get("data", {}).get("parsed", {}).get("info", {}) or {}
            symbol = meta.get("data", {}).get("symbol") or token_info.get("symbol") or ""
            name = meta.get("data", {}).get("name") or token_info.get("name") or ""
            supply_raw = token_info.get("supply")
            decimals = int(token_info.get("decimals") or 9)
            try:
                supply = float(supply_raw) / (10 ** decimals) if supply_raw else 0.0
            except (TypeError, ValueError):
                supply = 0.0
            self._record_success()
            return TokenData(
                mint=mint,
                symbol=symbol.strip(),
                name=name.strip(),
                chain="solana",
                decimals=decimals,
                supply=supply,
            )
        except Exception as exc:
            self._record_error(exc)
            logger.debug("Helius get_token_metadata error for %s: %s", mint[:12], exc)
            return None

    async def get_new_tokens(self, limit: int = 50) -> list[TokenData]:
        """
        Fetch recently created Solana tokens via Helius.

        Currently returns an empty list as a safe placeholder.  A proper
        implementation requires either:
        - A Helius webhook subscribed to ``TOKEN_MINT`` events, or
        - The Helius token-history/DAS search API for ``createdAt`` ordering.

        Using raw ``getProgramAccounts`` would stream millions of accounts
        and time out, so this method is intentionally conservative.  The
        primary discovery source is DexScreener; Helius adds value as a
        scan fallback via :meth:`get_token_metadata`.

        Parameters
        ----------
        limit:
            Ignored in the current implementation.

        Returns
        -------
        list[TokenData]
            Empty list (safe no-op until webhook integration is added).
        """
        logger.debug("Helius get_new_tokens: discovery via Helius not yet implemented; use DexScreener.")
        return []

    async def get_holder_count(self, mint: str) -> Optional[int]:
        """
        Return the number of token holder accounts for a mint.

        Uses a filtered ``getProgramAccounts`` call on the SPL Token program
        to count all accounts with non-zero balances for the given mint.

        Parameters
        ----------
        mint:
            The Solana token mint address.

        Returns
        -------
        int | None
            Number of holders, or None on error.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                {
                    "encoding": "jsonParsed",
                    "filters": [
                        {"dataSize": 165},
                        {"memcmp": {"offset": 0, "bytes": mint}},
                    ],
                    "commitment": "confirmed",
                },
            ],
        }
        try:
            response = await self._client.post(self._rpc_url, json=payload, timeout=15.0)
            if response.status_code != 200:
                return None
            data = response.json()
            accounts = data.get("result") or []
            count = 0
            for account in (accounts if isinstance(accounts, list) else []):
                info = (
                    account.get("account", {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                )
                ui_amount = info.get("tokenAmount", {}).get("uiAmount") or 0
                if ui_amount > 0:
                    count += 1
            self._record_success()
            return count
        except Exception as exc:
            self._record_error(exc)
            logger.debug("Helius get_holder_count error for %s: %s", mint[:12], exc)
            return None
