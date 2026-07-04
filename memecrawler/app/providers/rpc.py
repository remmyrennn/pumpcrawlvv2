"""
Solana RPC provider.

Provides direct Solana JSON-RPC access for on-chain data: account info,
token supply, and slot/epoch data.

Uses the public Solana mainnet RPC endpoint by default.

Sprint 1: health check only.
Sprint 2: token account info, mint supply lookup.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.providers.base import BaseProvider, ProviderStatus

logger = logging.getLogger(__name__)

_DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"


class SolanaRpcProvider(BaseProvider):
    """
    Provider for direct Solana JSON-RPC calls.

    Parameters
    ----------
    http_client:
        Shared async HTTP client.
    rpc_url:
        Solana RPC endpoint URL. Defaults to the public mainnet endpoint.

    Attributes
    ----------
    name:
        Registry key used by :class:`~app.providers.manager.ProviderManager`.
    """

    name = "rpc"
    version = "2.0.0"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        rpc_url: str = _DEFAULT_RPC_URL,
    ) -> None:
        super().__init__(http_client)
        self._rpc_url = rpc_url
        self._request_id: int = 0

    # ── Health ─────────────────────────────────────────────────────────────

    async def health_check(self) -> ProviderStatus:
        """
        Verify connectivity to the Solana RPC endpoint.

        Sends a ``getHealth`` JSON-RPC call and checks for an ``ok`` result.

        Returns
        -------
        ProviderStatus
            HEALTHY on success, DEGRADED/DOWN on error.
        """
        t0 = time.monotonic()
        try:
            response = await self._client.post(
                self._rpc_url,
                json=self._rpc_payload("getHealth"),
                timeout=10.0,
            )
            latency = (time.monotonic() - t0) * 1000
            data = response.json()
            if response.status_code == 200 and data.get("result") == "ok":
                self._record_success(latency_ms=latency)
                logger.debug("Solana RPC health check passed (%.0f ms).", latency)
            else:
                raise ValueError(
                    f"Unexpected RPC response: {response.status_code} {data}"
                )
        except Exception as exc:
            self._record_error(exc)
            logger.warning("Solana RPC health check failed: %s", exc)

        return self._status

    # ── RPC helpers ────────────────────────────────────────────────────────

    async def get_account_info(self, pubkey: str) -> Optional[dict[str, Any]]:
        """
        Fetch account information for a public key.

        Parameters
        ----------
        pubkey:
            Base-58 encoded public key.

        Returns
        -------
        dict | None
            The ``result.value`` from the RPC response, or ``None`` when
            the account does not exist or the call fails.
        """
        t0 = time.monotonic()
        try:
            payload = self._rpc_payload(
                "getAccountInfo",
                params=[pubkey, {"encoding": "jsonParsed"}],
            )
            response = await self._client.post(
                self._rpc_url, json=payload, timeout=10.0
            )
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            data = response.json()
            if "error" in data:
                raise ValueError(f"RPC error: {data['error']}")
            self._record_success(latency_ms=latency)
            return (data.get("result") or {}).get("value")
        except Exception as exc:
            self._record_error(exc)
            logger.warning("RPC getAccountInfo failed for %s: %s", pubkey[:12], exc)
            return None

    async def get_token_supply(self, mint: str) -> Optional[int]:
        """
        Return the total token supply for a mint address.

        Parameters
        ----------
        mint:
            Token mint address (base-58).

        Returns
        -------
        int | None
            Total supply in raw token units, or ``None`` on failure.
        """
        t0 = time.monotonic()
        try:
            payload = self._rpc_payload("getTokenSupply", params=[mint])
            response = await self._client.post(
                self._rpc_url, json=payload, timeout=10.0
            )
            latency = (time.monotonic() - t0) * 1000
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            data = response.json()
            if "error" in data:
                return None
            self._record_success(latency_ms=latency)
            amount_str = (
                (data.get("result") or {})
                .get("value", {})
                .get("amount", "")
            )
            return int(amount_str) if amount_str else None
        except Exception as exc:
            self._record_error(exc)
            logger.warning("RPC getTokenSupply failed for %s: %s", mint[:12], exc)
            return None

    # ── Internal ───────────────────────────────────────────────────────────

    def _rpc_payload(
        self,
        method: str,
        params: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 request payload."""
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            **({"params": params} if params is not None else {}),
        }
