"""Polymarket API client – fetches events, markets, and trades from the
Gamma and CLOB REST APIs using httpx (async)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Well-known entity keywords used by the extraction heuristic.  The list is
# intentionally short; the fallback is the market's category field.
# ---------------------------------------------------------------------------
_ENTITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # US Politics
    (re.compile(r"\b(Trump)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\b(Biden)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\b(Kamala|Harris)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\b(Obama)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\b(Kennedy|RFK)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\b(Republican|Democrat)\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\bPresidential Election\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\binaugurated\b", re.IGNORECASE), "US Elections"),
    (re.compile(r"\bpopular vote\b", re.IGNORECASE), "US Elections"),
    # Other elections
    (re.compile(r"\b(NYC|New York City).*mayor", re.IGNORECASE), "NYC Politics"),
    (re.compile(r"\bRomanian\b", re.IGNORECASE), "Romania"),
    (re.compile(r"\bSouth Korea\b", re.IGNORECASE), "South Korea"),
    (re.compile(r"\bZelenskyy|Ukraine\b", re.IGNORECASE), "Ukraine"),
    # Government / Monetary policy
    (re.compile(r"\bFed\b.*interest rate", re.IGNORECASE), "Federal Reserve"),
    (re.compile(r"\bFed\b.*bps", re.IGNORECASE), "Federal Reserve"),
    (re.compile(r"\b(Fed|Federal Reserve)\b", re.IGNORECASE), "Federal Reserve"),
    (re.compile(r"\b(SEC)\b"), "SEC"),
    (re.compile(r"\b(FDA)\b"), "FDA"),
    # Tech companies
    (re.compile(r"\b(Apple|AAPL)\b", re.IGNORECASE), "Apple"),
    (re.compile(r"\b(Google|Alphabet|GOOG)\b", re.IGNORECASE), "Google"),
    (re.compile(r"\b(Microsoft|MSFT)\b", re.IGNORECASE), "Microsoft"),
    (re.compile(r"\b(Amazon|AMZN)\b", re.IGNORECASE), "Amazon"),
    (re.compile(r"\b(Tesla|TSLA)\b", re.IGNORECASE), "Tesla"),
    (re.compile(r"\b(Meta|Facebook|META)\b", re.IGNORECASE), "Meta"),
    (re.compile(r"\b(Nvidia|NVDA)\b", re.IGNORECASE), "Nvidia"),
    (re.compile(r"\b(OpenAI)\b", re.IGNORECASE), "OpenAI"),
    (re.compile(r"\b(SpaceX)\b", re.IGNORECASE), "SpaceX"),
    # Sports
    (re.compile(r"\bNBA\b|NBA Finals", re.IGNORECASE), "NBA"),
    (re.compile(r"\bSuper Bowl\b|NFL\b", re.IGNORECASE), "NFL"),
    (re.compile(r"\bChampions League\b|UEFA\b", re.IGNORECASE), "UEFA"),
    (re.compile(r"\bPremier League\b", re.IGNORECASE), "Premier League"),
    (re.compile(r"\bLa Liga\b", re.IGNORECASE), "La Liga"),
    (re.compile(r"\bStanley Cup\b|NHL\b", re.IGNORECASE), "NHL"),
    # Geopolitics
    (re.compile(r"\b(China)\b", re.IGNORECASE), "China"),
    (re.compile(r"\b(Russia)\b", re.IGNORECASE), "Russia/Ukraine"),
    # Crypto
    (re.compile(r"\b(Bitcoin|BTC)\b", re.IGNORECASE), "Bitcoin"),
    (re.compile(r"\b(Ethereum|ETH)\b", re.IGNORECASE), "Ethereum"),
    (re.compile(r"\b(Solana|SOL)\b", re.IGNORECASE), "Solana"),
]


def _extract_entity(question: str, tags: list[str] | None, category: str) -> str:
    """Try to extract a meaningful entity name from the market question/tags.

    Heuristic order:
    1.  Match against known entity patterns in the question text.
    2.  If the market has tags, use the first non-generic tag.
    3.  Fall back to the category field.
    """
    # 1. Regex against known entities
    for pattern, entity_name in _ENTITY_PATTERNS:
        if pattern.search(question):
            return entity_name

    # 2. Tags (skip very generic ones)
    _GENERIC_TAGS = {"politics", "crypto", "sports", "science", "entertainment", "other"}
    if tags:
        for tag in tags:
            if tag.lower() not in _GENERIC_TAGS and len(tag) > 1:
                return tag.title()

    # 3. Category fallback
    return category or "Unknown"


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class PolymarketClient:
    """Async client wrapping Polymarket Gamma + CLOB APIs."""

    def __init__(
        self,
        gamma_url: str = settings.polymarket_gamma_url,
        clob_url: str = settings.polymarket_clob_url,
    ) -> None:
        self.gamma_url = gamma_url.rstrip("/")
        self.clob_url = clob_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    # -- Gamma API ---------------------------------------------------------

    async def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        closed: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch events from the Gamma API, ordered by volume."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "closed": str(closed).lower(),
            "order": "volume",
            "ascending": "false",
        }
        resp = await self._client.get(f"{self.gamma_url}/events", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_markets(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch resolved/closed markets from the Gamma API."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "closed": "true",
        }
        resp = await self._client.get(f"{self.gamma_url}/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict[str, Any] | None:
        """Fetch a single market by its condition_id.

        The Gamma API's ``condition_id`` filter is unreliable, so we
        fetch a batch and filter client-side by exact conditionId match.
        Falls back to the most recently created result if no exact match.
        """
        params: dict[str, Any] = {
            "condition_id": condition_id,
            "limit": 100,
        }
        resp = await self._client.get(f"{self.gamma_url}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None

        # Filter for exact conditionId match
        exact = [m for m in data if m.get("conditionId") == condition_id]
        if exact:
            if len(exact) == 1:
                return exact[0]
            return max(exact, key=lambda m: m.get("createdAt", ""))

        # No exact match — the API filter is broken, return None
        return None

    async def get_market_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Fetch a single market by its slug (reliable filter)."""
        params = {"slug": slug}
        resp = await self._client.get(f"{self.gamma_url}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

    # -- Data API (public, no auth) ----------------------------------------

    async def get_trades(
        self,
        condition_id: str,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Fetch trades for a market from the Polymarket Data API.

        Uses ``data-api.polymarket.com/trades`` which is public and returns
        trades with ``proxyWallet``, ``side``, ``size``, ``price``,
        ``outcome``, and ``timestamp`` fields.
        """
        all_trades: list[dict[str, Any]] = []
        offset = 0
        batch_size = min(limit, 1_000)  # Data API caps at 1000 per request

        while len(all_trades) < limit:
            params: dict[str, Any] = {
                "market": condition_id,
                "limit": batch_size,
                "offset": offset,
            }
            try:
                resp = await self._client.get(
                    "https://data-api.polymarket.com/trades", params=params
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                # API may reject high offsets — return what we have
                logger.info(
                    "Data API returned error at offset %d, returning %d trades collected so far",
                    offset, len(all_trades),
                )
                break
            batch = resp.json()
            if not batch:
                break
            all_trades.extend(batch)
            offset += len(batch)
            if len(batch) < batch_size:
                break

        return all_trades[:limit]

    # -- Data API: holders (E1) -------------------------------------------

    async def get_holders(
        self,
        condition_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch current holders for a market from the Polymarket Data API.

        Calls ``GET https://data-api.polymarket.com/holders`` with the
        market condition ID.  Returns a list of holder dicts with
        ``proxyWallet``, ``outcome``, ``amount``, ``valueUsd``, etc.
        """
        params: dict[str, Any] = {
            "market": condition_id,
            "limit": limit,
        }
        try:
            resp = await self._client.get(
                "https://data-api.polymarket.com/holders", params=params
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Failed to fetch holders for %s: %s", condition_id, exc)
            return []

    # -- CLOB API: price history (E2) -------------------------------------

    async def get_price_history(
        self,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        interval: str = "1h",
        fidelity: int = 60,
    ) -> list[dict[str, Any]]:
        """Fetch CLOB price history for a token.

        Calls ``GET https://clob.polymarket.com/prices-history`` with
        the given token ID and time range parameters.

        Returns a list of dicts with ``t`` (Unix timestamp) and ``p``
        (price) fields.
        """
        params: dict[str, Any] = {
            "market": token_id,
            "interval": interval,
            "fidelity": fidelity,
        }
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts

        try:
            resp = await self._client.get(
                f"{self.clob_url}/prices-history", params=params
            )
            resp.raise_for_status()
            data = resp.json()
            # The API may return {"history": [...]} or a bare list
            if isinstance(data, dict):
                return data.get("history", [])
            return data if isinstance(data, list) else []
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Failed to fetch price history for token %s: %s", token_id, exc)
            return []

    # -- Data API: wallet activity (E3) -----------------------------------

    async def get_wallet_activity(
        self,
        wallet_address: str,
        limit: int = 100,
        activity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch activity for a specific wallet from the Polymarket Data API.

        Calls ``GET https://data-api.polymarket.com/activity`` with the
        proxy wallet address.  Returns a list of activity dicts.

        Parameters
        ----------
        wallet_address:
            The proxy wallet address.
        limit:
            Maximum number of activities to return.
        activity_types:
            Optional list of activity type filters (e.g. ["TRADE", "DEPOSIT"]).
        """
        params: dict[str, Any] = {
            "user": wallet_address,
            "limit": limit,
        }
        if activity_types:
            params["type"] = ",".join(activity_types)

        try:
            resp = await self._client.get(
                "https://data-api.polymarket.com/activity", params=params
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "Failed to fetch activity for wallet %s: %s", wallet_address, exc
            )
            return []

    # -- Mapping helpers ---------------------------------------------------

    def map_market(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map a Gamma API market dict to fields matching our ``Market`` model.

        Returns a plain dict suitable for constructing a ``Market`` ORM
        instance or upserting into the database.
        """
        question: str = raw.get("question", "")
        slug: str = raw.get("slug", "")
        category: str = raw.get("category", "Unknown")
        tags: list[str] | None = raw.get("tags")

        entity = _extract_entity(question, tags, category)

        # Resolution: derive from outcomePrices — [yesPrice, noPrice]
        # If yesPrice ≈ 1.0 → resolved Yes, if ≈ 0.0 → resolved No
        resolution = "unknown"
        outcome_prices = raw.get("outcomePrices")
        if outcome_prices:
            try:
                if isinstance(outcome_prices, str):
                    import json as _json
                    prices = _json.loads(outcome_prices)
                else:
                    prices = outcome_prices
                if prices and float(prices[0]) > 0.9:
                    resolution = "Yes"
                elif prices and float(prices[0]) < 0.1:
                    resolution = "No"
            except (ValueError, IndexError, TypeError):
                pass
        if resolution == "unknown":
            resolution = raw.get("resolution", raw.get("outcome", "unknown")) or "unknown"

        resolved_at = _parse_iso(
            raw.get("resolved_at") or raw.get("closedTime") or raw.get("endDate")
        )
        created_at = _parse_iso(raw.get("createdAt") or raw.get("startDate"))
        if created_at is None:
            created_at = datetime.now(timezone.utc)

        # Sanity check: resolved_at must be after created_at.  If not,
        # the closedTime is stale (e.g. condition_id reused across years).
        # Fall back to endDate, then to None.
        if resolved_at and created_at and resolved_at < created_at:
            end_date_parsed = _parse_iso(raw.get("endDate"))
            if end_date_parsed and end_date_parsed >= created_at:
                resolved_at = end_date_parsed
            else:
                resolved_at = None

        volume = float(raw.get("volume", 0) or 0)
        liquidity = float(raw.get("liquidityNum", 0) or 0)
        open_interest = float(raw.get("openInterest", 0) or 0)
        volume_24hr = float(raw.get("volume24hr", 0) or 0)
        end_date = _parse_iso(raw.get("endDate"))

        # clobTokenIds — needed for CLOB price history and WebSocket subscriptions
        clob_token_ids_raw = raw.get("clobTokenIds")
        if clob_token_ids_raw:
            if isinstance(clob_token_ids_raw, str):
                clob_token_ids = clob_token_ids_raw  # already JSON string
            else:
                import json as _json
                clob_token_ids = _json.dumps(clob_token_ids_raw)
        else:
            clob_token_ids = None

        # Use conditionId as the canonical market id
        market_id = raw.get("conditionId") or raw.get("condition_id") or raw.get("id", "")

        return {
            "id": str(market_id),
            "question": question,
            "slug": slug,
            "entity": entity,
            "category": category,
            "resolution": str(resolution),
            "resolved_at": resolved_at,
            "created_at": created_at,
            "volume": volume,
            "liquidity": liquidity,
            "open_interest": open_interest,
            "volume_24hr": volume_24hr,
            "clob_token_ids": clob_token_ids,
            "end_date": end_date,
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


# Module-level singleton
polymarket_client = PolymarketClient()
