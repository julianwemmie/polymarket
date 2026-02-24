"""PolygonScan blockchain client – wallet creation dates, transactions,
token transfers, and funding source analysis.

Uses the Etherscan V2 API with chainid=137 for Polygon PoS.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# Etherscan V2 free-tier rate limit: 5 requests / second.
# Global semaphore ensures all concurrent callers share the budget.
_RATE_SEMAPHORE = asyncio.Semaphore(4)
_RATE_LIMIT_DELAY: float = 0.25  # 4 req/s to stay safely under the cap


class BlockchainClient:
    """Async client for the Etherscan V2 API (Polygon PoS chain)."""

    def __init__(
        self,
        base_url: str = settings.polygonscan_url,
        api_key: str = settings.polygonscan_api_key,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)
        self._consecutive_failures: int = 0
        self._skip_remaining: bool = False

    async def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Issue a rate-limited GET and return the ``result`` list.

        Etherscan wraps every response in ``{"status": "1", "result": [...]}``.
        If the call fails or returns an error status the method logs a
        warning and returns an empty list rather than raising.

        Uses a global semaphore to enforce rate limits across concurrent callers.
        After 10 consecutive failures, skips remaining calls for this run.
        """
        if self._skip_remaining:
            return []

        params["apikey"] = self.api_key
        params["chainid"] = 137  # Polygon PoS

        async with _RATE_SEMAPHORE:
            await asyncio.sleep(_RATE_LIMIT_DELAY)

            resp = await self._client.get(self.base_url, params=params)
            resp.raise_for_status()
            body = resp.json()

        if body.get("status") != "1":
            message = body.get("message", "unknown error")
            # "No transactions found" is a valid empty result, not a real error
            if "no transactions found" in message.lower():
                self._consecutive_failures = 0
                return []
            logger.warning("PolygonScan API error: %s", message)
            self._consecutive_failures += 1
            if self._consecutive_failures >= 10:
                logger.warning(
                    "PolygonScan: %d consecutive failures, skipping remaining calls this run",
                    self._consecutive_failures,
                )
                self._skip_remaining = True
            return []

        self._consecutive_failures = 0
        result = body.get("result", [])
        if not isinstance(result, list):
            return []
        return result

    def reset(self) -> None:
        """Reset failure tracking for a new ingestion run."""
        self._consecutive_failures = 0
        self._skip_remaining = False

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get_wallet_creation_date(self, address: str) -> datetime | None:
        """Return the timestamp of the wallet's first on-chain transaction.

        Fetches the earliest normal transaction (sorted ascending, limit 1)
        and converts the Unix timestamp to a timezone-aware ``datetime``.
        Returns ``None`` if there are no transactions.
        """
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 1,
            "sort": "asc",
        }
        txns = await self._get(params)
        if not txns:
            return None
        try:
            ts = int(txns[0]["timeStamp"])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (KeyError, ValueError, TypeError):
            return None

    async def get_wallet_transactions(
        self,
        address: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the most recent normal transactions for *address*."""
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",
        }
        return await self._get(params)

    async def get_token_transfers(
        self,
        address: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return ERC-20 token transfers for *address*."""
        params = {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",
        }
        return await self._get(params)

    async def get_funding_source(self, address: str) -> str | None:
        """Identify the wallet that first sent funds to *address*.

        Looks at the earliest normal transaction (sorted ascending) where
        ``to`` matches *address* and returns the ``from`` field.  Returns
        ``None`` if no incoming transaction is found.
        """
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 10,  # small batch; we only need the first match
            "sort": "asc",
        }
        txns = await self._get(params)
        addr_lower = address.lower()
        for tx in txns:
            if tx.get("to", "").lower() == addr_lower:
                return tx.get("from")
        return None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


# Module-level singleton
blockchain_client = BlockchainClient()
