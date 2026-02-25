"""Shared wallet history utilities used by routers and analysis tasks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.services.polymarket import polymarket_client


@dataclass
class WalletHistoryMarketRecord:
    condition_id: str
    title: str
    outcome_bought: str
    side: str
    trades: int
    total_size: float
    total_cost: float
    resolved: bool
    won: bool | None


@dataclass
class WalletHistorySummary:
    address: str
    total_trades: int
    total_markets: int
    resolved_markets: int
    wins: int
    losses: int
    win_rate: float | None
    markets: list[WalletHistoryMarketRecord]


def _normalize_binary(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"yes", "1", "true"}:
        return "yes"
    if normalized in {"no", "0", "false"}:
        return "no"
    return None


def _resolve_winning_outcome(market_data: dict) -> str | None:
    outcome = market_data.get("outcome") or market_data.get("resolution")
    if outcome:
        return str(outcome)

    prices = market_data.get("outcomePrices")
    try:
        if isinstance(prices, str):
            import json as _json

            prices = _json.loads(prices)
        if isinstance(prices, list) and prices:
            yes_price = float(prices[0])
            if yes_price > 0.9:
                return "Yes"
            if yes_price < 0.1:
                return "No"
    except (ValueError, TypeError, IndexError):
        pass

    return None


def _determine_win(
    dominant_outcome: str,
    dominant_idx: int,
    winning_outcome: str,
) -> bool | None:
    dominant_binary = _normalize_binary(dominant_outcome)
    winning_binary = _normalize_binary(winning_outcome)

    if dominant_binary and winning_binary:
        return dominant_binary == winning_binary

    if winning_binary == "yes":
        return dominant_idx == 0
    if winning_binary == "no":
        return dominant_idx == 1

    if dominant_outcome.strip().lower() == winning_outcome.strip().lower():
        return True

    return None


async def _resolve_market_status(
    condition_id: str,
    slug: str | None,
    resolution_cache: dict[str, dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    cached = resolution_cache.get(condition_id)
    if cached is not None:
        return cached

    async with semaphore:
        market_data = None
        try:
            market_data = await polymarket_client.get_market(condition_id)
            if market_data is None and slug:
                market_data = await polymarket_client.get_market_by_slug(slug)
        except Exception:
            market_data = None

    winning_outcome = None
    resolved = False
    if market_data:
        winning_outcome = _resolve_winning_outcome(market_data)
        is_closed = bool(market_data.get("closed") is True or market_data.get("active") is False)
        resolved = bool(winning_outcome and is_closed)

    status = {
        "resolved": resolved,
        "winning_outcome": winning_outcome,
    }
    resolution_cache[condition_id] = status
    return status


async def fetch_wallet_full_history(
    address: str,
    resolution_cache: dict[str, dict[str, Any]] | None = None,
) -> WalletHistorySummary:
    """Fetch and score full wallet trade history from Polymarket Data API."""
    activities = await polymarket_client.get_wallet_activity(
        wallet_address=address,
        activity_types=["TRADE"],
    )

    by_market: dict[str, list[dict]] = defaultdict(list)
    for a in activities:
        cid = str(a.get("conditionId") or "").strip()
        if cid:
            by_market[cid].append(a)

    cache = resolution_cache if resolution_cache is not None else {}
    resolution_semaphore = asyncio.Semaphore(8)

    slugs_by_market: dict[str, str | None] = {}
    for cid, trades in by_market.items():
        slug = str(trades[0].get("slug") or "").strip() or None
        slugs_by_market[cid] = slug

    condition_ids = list(by_market.keys())
    resolution_tasks = [
        _resolve_market_status(
            condition_id=cid,
            slug=slugs_by_market.get(cid),
            resolution_cache=cache,
            semaphore=resolution_semaphore,
        )
        for cid in condition_ids
    ]

    resolution_results = await asyncio.gather(*resolution_tasks)
    resolution_by_market = {
        cid: result for cid, result in zip(condition_ids, resolution_results, strict=False)
    }

    market_records: list[WalletHistoryMarketRecord] = []
    wins = 0
    losses = 0
    resolved_count = 0

    for cid, trades in by_market.items():
        title = str(trades[0].get("title") or "Unknown")

        net_by_outcome: dict[int, float] = defaultdict(float)
        total_size = 0.0
        total_cost = 0.0

        for t in trades:
            side = str(t.get("side", "BUY") or "BUY").upper()
            size = float(t.get("size", 0) or 0)
            cost = float(t.get("usdcSize", 0) or 0)
            outcome_idx = int(t.get("outcomeIndex", 0) or 0)
            total_size += size
            total_cost += cost
            if side == "BUY":
                net_by_outcome[outcome_idx] += size
            else:
                net_by_outcome[outcome_idx] -= size

        dominant_idx = max(net_by_outcome, key=lambda k: net_by_outcome[k]) if net_by_outcome else 0
        dominant_outcome = str(trades[0].get("outcome", "Unknown") or "Unknown")
        for t in trades:
            if int(t.get("outcomeIndex", -1) or -1) == dominant_idx:
                dominant_outcome = str(t.get("outcome", "Unknown") or "Unknown")
                break

        status = resolution_by_market.get(cid, {"resolved": False, "winning_outcome": None})
        resolved = bool(status.get("resolved", False))
        winning_outcome = status.get("winning_outcome")

        won: bool | None = None
        if resolved and winning_outcome:
            resolved_count += 1
            won = _determine_win(
                dominant_outcome=dominant_outcome,
                dominant_idx=dominant_idx,
                winning_outcome=str(winning_outcome),
            )
            if won is True:
                wins += 1
            elif won is False:
                losses += 1

        net_side = "BUY" if net_by_outcome.get(dominant_idx, 0) > 0 else "SELL"

        market_records.append(
            WalletHistoryMarketRecord(
                condition_id=cid,
                title=title,
                outcome_bought=dominant_outcome,
                side=net_side,
                trades=len(trades),
                total_size=total_size,
                total_cost=total_cost,
                resolved=resolved,
                won=won,
            )
        )

    market_records.sort(key=lambda m: (not m.resolved, -m.total_cost))
    win_rate = wins / resolved_count if resolved_count > 0 else None

    return WalletHistorySummary(
        address=address,
        total_trades=len(activities),
        total_markets=len(by_market),
        resolved_markets=resolved_count,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        markets=market_records,
    )
