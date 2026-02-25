"""Background task pipeline for entity-based wallet analysis."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import async_session
from src.models import Entity, EntityMarket, EntityWalletScore, Wallet
from src.services.entity_scoring import entity_scoring_engine
from src.services.polymarket import polymarket_client
from src.services.wallet_history import fetch_wallet_full_history

logger = logging.getLogger(__name__)
OVERALL_HISTORY_TIMEOUT_S = 45


entity_progress: dict[int, dict[str, Any]] = {}


def _default_progress() -> dict[str, Any]:
    return {
        "running": False,
        "done": False,
        "stage": "idle",
        "current": 0,
        "total": 0,
        "current_market": "",
        "wallet_current": 0,
        "wallet_total": 0,
        "current_wallet": "",
        "wallet_stage": "",
        "resolved_markets": 0,
        "error": None,
    }


def get_entity_progress(entity_id: int) -> dict[str, Any]:
    return entity_progress.get(entity_id, _default_progress())


def _set_progress(entity_id: int, **fields: Any) -> None:
    state = entity_progress.setdefault(entity_id, _default_progress())
    state.update(fields)


def _normalize_binary(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"yes", "1", "true"}:
        return "yes"
    if normalized in {"no", "0", "false"}:
        return "no"
    return None


def _extract_winning_outcome(market_data: dict[str, Any]) -> str | None:
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


def _is_market_resolved(market_data: dict[str, Any], winning_outcome: str | None) -> bool:
    if not winning_outcome:
        return False
    return bool(market_data.get("closed") is True or market_data.get("active") is False)


def _parse_trade(raw_trade: dict[str, Any], fallback_market_id: str, idx: int) -> dict[str, Any] | None:
    wallet_addr = (
        raw_trade.get("proxyWallet")
        or raw_trade.get("taker_address")
        or raw_trade.get("maker_address")
        or ""
    )
    wallet_addr = str(wallet_addr).lower().strip()
    if not wallet_addr:
        return None

    trade_id = str(
        raw_trade.get("transactionHash")
        or raw_trade.get("id")
        or f"{fallback_market_id}-{idx}"
    )

    try:
        outcome_index = int(raw_trade.get("outcomeIndex", -1) or -1)
    except (ValueError, TypeError):
        outcome_index = -1

    return {
        "trade_id": trade_id,
        "wallet": wallet_addr,
        "side": str(raw_trade.get("side") or "BUY").upper(),
        "outcome": str(raw_trade.get("outcome") or "Unknown"),
        "outcome_index": outcome_index,
        "amount": abs(float(raw_trade.get("size") or raw_trade.get("amount") or 0)),
        "price": float(raw_trade.get("price") or 0),
    }


def _trade_on_winner(trade: dict[str, Any], winning_outcome: str) -> bool | None:
    winning_binary = _normalize_binary(winning_outcome)
    trade_binary = _normalize_binary(trade.get("outcome"))

    if winning_binary and trade_binary:
        return trade_binary == winning_binary

    if winning_binary and trade.get("outcome_index") in {0, 1}:
        return (
            trade["outcome_index"] == 0
            if winning_binary == "yes"
            else trade["outcome_index"] == 1
        )

    if str(trade.get("outcome", "")).strip().lower() == str(winning_outcome).strip().lower():
        return True

    return None


def _compute_wallet_market_result(
    trades: list[dict[str, Any]],
    market_meta: dict[str, Any],
) -> dict[str, Any]:
    resolved = bool(market_meta.get("resolved", False))
    winning_outcome = market_meta.get("winning_outcome")

    if not resolved or not winning_outcome:
        return {
            "resolved": False,
            "won": None,
            "profit": 0.0,
        }

    net_by_outcome: dict[int, float] = defaultdict(float)
    for t in trades:
        idx = int(t.get("outcome_index", -1))
        if idx < 0:
            continue
        if t.get("side") == "BUY":
            net_by_outcome[idx] += float(t.get("amount", 0))
        else:
            net_by_outcome[idx] -= float(t.get("amount", 0))

    dominant_idx = max(net_by_outcome, key=lambda k: net_by_outcome[k]) if net_by_outcome else -1
    dominant_outcome = ""
    if dominant_idx >= 0:
        for t in trades:
            if int(t.get("outcome_index", -2)) == dominant_idx:
                dominant_outcome = str(t.get("outcome", ""))
                break

    dominant_binary = _normalize_binary(dominant_outcome)
    winning_binary = _normalize_binary(str(winning_outcome))

    won: bool | None = None
    if dominant_binary and winning_binary:
        won = dominant_binary == winning_binary
    elif winning_binary == "yes" and dominant_idx == 0:
        won = True
    elif winning_binary == "no" and dominant_idx == 1:
        won = True
    elif dominant_outcome and dominant_outcome.strip().lower() == str(winning_outcome).strip().lower():
        won = True
    elif dominant_idx >= 0:
        won = False

    profit = 0.0
    for t in trades:
        on_winner = _trade_on_winner(t, str(winning_outcome))
        if on_winner is None:
            continue

        amount = float(t.get("amount", 0))
        price = float(t.get("price", 0))
        side = str(t.get("side", "BUY")).upper()

        if side == "BUY":
            profit += amount * (1.0 - price) if on_winner else -amount * price
        else:
            profit += -amount * (1.0 - price) if on_winner else amount * price

    return {
        "resolved": True,
        "won": won,
        "profit": profit,
    }


async def _get_or_create_wallet(
    db: AsyncSession,
    wallet_cache: dict[str, Wallet],
    address: str,
) -> Wallet:
    if address in wallet_cache:
        return wallet_cache[address]

    result = await db.execute(select(Wallet).where(Wallet.address == address))
    wallet = result.scalar_one_or_none()

    if wallet is None:
        wallet = Wallet(
            address=address,
            first_seen=None,
            market_count=0,
            total_volume=0.0,
            total_profit=0.0,
            suspicion_score=0.0,
            funding_source=None,
            win_count=0,
            loss_count=0,
            win_rate=0.0,
        )
        db.add(wallet)
        await db.flush()

    wallet_cache[address] = wallet
    return wallet


async def _get_overall_stats(
    db: AsyncSession,
    wallet: Wallet,
    resolution_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int, int, float | None]:
    now = datetime.now(timezone.utc)

    if wallet.full_history_fetched_at is not None:
        cached_at = wallet.full_history_fetched_at
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)

        if (
            now - cached_at < timedelta(hours=24)
            and wallet.overall_wins_cached is not None
            and wallet.overall_losses_cached is not None
        ):
            overall_wins = int(wallet.overall_wins_cached)
            overall_losses = int(wallet.overall_losses_cached)
            overall_markets = overall_wins + overall_losses
            return (
                overall_markets,
                overall_wins,
                overall_losses,
                (
                    float(wallet.overall_win_rate_cached)
                    if wallet.overall_win_rate_cached is not None
                    else None
                ),
            )

    try:
        summary = await asyncio.wait_for(
            fetch_wallet_full_history(
                wallet.address,
                resolution_cache=resolution_cache,
            ),
            timeout=OVERALL_HISTORY_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, Exception):
        logger.warning(
            "Falling back to cached/legacy overall stats for wallet %s",
            wallet.address,
        )
        # If full-history fetch fails, keep the pipeline moving with whatever
        # wallet-level stats we already have (or mark as unavailable).
        fallback_wins = wallet.overall_wins_cached
        fallback_losses = wallet.overall_losses_cached
        fallback_rate = wallet.overall_win_rate_cached

        if fallback_wins is None or fallback_losses is None:
            if wallet.win_count > 0 or wallet.loss_count > 0:
                fallback_wins = wallet.win_count
                fallback_losses = wallet.loss_count
                total = fallback_wins + fallback_losses
                fallback_rate = (fallback_wins / total) if total > 0 else None
            else:
                return 0, 0, 0, None

        overall_wins = int(fallback_wins or 0)
        overall_losses = int(fallback_losses or 0)
        overall_markets = overall_wins + overall_losses
        return (
            overall_markets,
            overall_wins,
            overall_losses,
            float(fallback_rate) if fallback_rate is not None else None,
        )

    wallet.overall_wins_cached = summary.wins
    wallet.overall_losses_cached = summary.losses
    wallet.overall_win_rate_cached = summary.win_rate
    wallet.full_history_fetched_at = now

    wallet.win_count = summary.wins
    wallet.loss_count = summary.losses
    wallet.win_rate = summary.win_rate or 0.0

    await db.flush()

    return summary.resolved_markets, summary.wins, summary.losses, summary.win_rate


async def _mark_entity_error(entity_id: int, error: str) -> None:
    async with async_session() as db:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            return
        entity.status = "error"
        entity.error_message = error
        await db.commit()


async def run_entity_analysis(entity_id: int) -> None:
    """Analyze selected markets for an entity and persist wallet score rows."""
    _set_progress(
        entity_id,
        running=True,
        done=False,
        stage="ingesting",
        current=0,
        total=0,
        wallet_current=0,
        wallet_total=0,
        current_market="",
        current_wallet="",
        wallet_stage="",
        resolved_markets=0,
        error=None,
    )

    wallet_cache: dict[str, Wallet] = {}
    resolution_cache: dict[str, dict[str, Any]] = {}

    try:
        async with async_session() as db:
            entity_stmt = (
                select(Entity)
                .where(Entity.id == entity_id)
                .options(selectinload(Entity.markets))
            )
            entity_result = await db.execute(entity_stmt)
            entity = entity_result.scalar_one_or_none()
            if entity is None:
                _set_progress(
                    entity_id,
                    running=False,
                    done=True,
                    stage="error",
                    error="Entity not found",
                )
                return

            included_markets = [m for m in entity.markets if m.included]
            if not included_markets:
                entity.status = "error"
                entity.error_message = "No included markets selected"
                await db.commit()
                _set_progress(
                    entity_id,
                    running=False,
                    done=True,
                    stage="error",
                    error="No included markets selected",
                )
                return

            entity.status = "ingesting"
            entity.error_message = None
            entity.included_market_count = len(included_markets)
            await db.commit()

            market_wallet_trades: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
                lambda: defaultdict(list)
            )
            market_meta: dict[str, dict[str, Any]] = {}
            resolved_market_count = 0

            _set_progress(entity_id, stage="ingesting", current=0, total=len(included_markets))

            for idx, entity_market in enumerate(included_markets, 1):
                _set_progress(
                    entity_id,
                    current=idx,
                    total=len(included_markets),
                    current_market=entity_market.question,
                )

                latest_market = await polymarket_client.get_market(entity_market.condition_id)
                if latest_market:
                    if latest_market.get("question"):
                        entity_market.question = str(latest_market.get("question"))
                    if latest_market.get("slug"):
                        entity_market.slug = str(latest_market.get("slug"))
                    entity_market.volume = float(
                        latest_market.get("volumeNum", latest_market.get("volume", entity_market.volume))
                        or 0
                    )

                    updated_winning_outcome = _extract_winning_outcome(latest_market)
                    if updated_winning_outcome:
                        entity_market.winning_outcome = updated_winning_outcome

                    entity_market.resolved = _is_market_resolved(
                        latest_market,
                        entity_market.winning_outcome,
                    )

                if entity_market.resolved:
                    resolved_market_count += 1

                market_meta[entity_market.condition_id] = {
                    "question": entity_market.question,
                    "resolved": bool(entity_market.resolved),
                    "winning_outcome": entity_market.winning_outcome,
                    "volume": entity_market.volume,
                }

                raw_trades = await polymarket_client.get_trades(
                    condition_id=entity_market.condition_id,
                    limit=10_000,
                )

                seen_trade_ids: set[str] = set()
                for trade_idx, raw_trade in enumerate(raw_trades):
                    parsed = _parse_trade(raw_trade, entity_market.condition_id, trade_idx)
                    if not parsed:
                        continue
                    if parsed["trade_id"] in seen_trade_ids:
                        continue
                    seen_trade_ids.add(parsed["trade_id"])
                    market_wallet_trades[parsed["wallet"]][entity_market.condition_id].append(parsed)

            entity.discovered_market_count = len(entity.markets)
            await db.commit()

            candidate_wallets = [
                wallet
                for wallet, per_market in market_wallet_trades.items()
                if len(per_market) >= 2
            ]

            await db.execute(delete(EntityWalletScore).where(EntityWalletScore.entity_id == entity_id))
            await db.flush()

            entity.status = "scoring"
            await db.commit()

            _set_progress(
                entity_id,
                stage="scoring",
                resolved_markets=resolved_market_count,
                wallet_total=len(candidate_wallets),
                wallet_current=0,
            )

            scored_count = 0
            flagged_count = 0

            for idx, wallet_address in enumerate(candidate_wallets, 1):
                _set_progress(
                    entity_id,
                    wallet_current=idx,
                    current_wallet=wallet_address,
                    wallet_stage="entity-stats",
                )

                wallet = await _get_or_create_wallet(db, wallet_cache, wallet_address)

                per_market = market_wallet_trades[wallet_address]
                entity_wins = 0
                entity_losses = 0
                entity_profit = 0.0
                entity_resolved_markets = 0
                breakdown: list[dict[str, Any]] = []

                for condition_id, trades in per_market.items():
                    meta = market_meta.get(condition_id, {})
                    result = _compute_wallet_market_result(trades, meta)

                    if result["resolved"] and result["won"] is not None:
                        entity_resolved_markets += 1
                        if result["won"]:
                            entity_wins += 1
                        else:
                            entity_losses += 1

                    entity_profit += float(result["profit"])

                    breakdown.append(
                        {
                            "condition_id": condition_id,
                            "question": meta.get("question", condition_id),
                            "resolved": bool(result["resolved"]),
                            "won": result["won"],
                            "profit": round(float(result["profit"]), 4),
                            "trade_count": len(trades),
                            "winning_outcome": meta.get("winning_outcome"),
                        }
                    )

                _set_progress(
                    entity_id,
                    wallet_stage="overall-history",
                )
                (
                    overall_markets,
                    overall_wins,
                    overall_losses,
                    overall_win_rate,
                ) = await _get_overall_stats(
                    db,
                    wallet,
                    resolution_cache=resolution_cache,
                )

                score = entity_scoring_engine.score_wallet(
                    entity_wins=entity_wins,
                    entity_losses=entity_losses,
                    overall_wins=overall_wins,
                    overall_losses=overall_losses,
                )

                row = EntityWalletScore(
                    entity_id=entity_id,
                    wallet_address=wallet.address,
                    entity_markets_traded=len(per_market),
                    entity_resolved_markets=entity_resolved_markets,
                    entity_wins=entity_wins,
                    entity_losses=entity_losses,
                    entity_win_rate=score.entity_win_rate,
                    entity_profit=round(entity_profit, 4),
                    overall_markets=overall_markets,
                    overall_wins=overall_wins,
                    overall_losses=overall_losses,
                    overall_win_rate=overall_win_rate,
                    win_rate_delta=score.win_rate_delta,
                    suspicion_score=score.suspicion_score,
                    is_flagged=score.is_flagged,
                    reasons=score.reasons,
                    market_breakdown=breakdown,
                )
                db.add(row)

                scored_count += 1
                if score.is_flagged:
                    flagged_count += 1

                if idx % 10 == 0:
                    entity.scored_wallet_count = scored_count
                    entity.flagged_wallet_count = flagged_count
                    await db.commit()

            entity.status = "done"
            entity.scored_wallet_count = scored_count
            entity.flagged_wallet_count = flagged_count
            entity.error_message = None
            await db.commit()

            _set_progress(
                entity_id,
                running=False,
                done=True,
                stage="done",
                wallet_current=len(candidate_wallets),
                wallet_total=len(candidate_wallets),
                resolved_markets=resolved_market_count,
            )

    except Exception as exc:  # pragma: no cover - best effort progress reporting
        logger.exception("Entity analysis failed for entity_id=%s", entity_id)
        await _mark_entity_error(entity_id, str(exc))
        _set_progress(
            entity_id,
            running=False,
            done=True,
            stage="error",
            error=str(exc),
        )
