"""Data ingestion script for the Polymarket Insider Trading Detector.

Fetches resolved markets from Polymarket's Gamma API, retrieves trades
from the CLOB API, scores each wallet-market pair through the win-rate-
primary suspicion engine, and persists everything to the database.

Usage:
    python -m src.tasks.ingest            # ingest 20 markets (default)
    python -m src.tasks.ingest --limit 50 # ingest 50 markets
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import async_session, init_db
from src.models import Market, MarketHolder, PriceSnapshot, SuspicionFlag, Trade, Wallet
from src.services.polymarket import polymarket_client
from src.services.suspicion import SuspicionEngine, suspicion_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared progress state -- read by the /api/ingest/progress SSE endpoint
# ---------------------------------------------------------------------------
progress: dict[str, Any] = {
    "running": False,
    "current": 0,
    "total": 0,
    "current_market": "",
    "markets_done": [],  # list of {question, wallets, suspicious, score}
    "error": None,
}


# ---------------------------------------------------------------------------
# Helper: parse ISO timestamps coming back from the CLOB API
# ---------------------------------------------------------------------------


def _parse_ts(value: str | int | float | None) -> datetime:
    """Best-effort conversion of a CLOB trade timestamp to a datetime.

    The CLOB API may return ISO-8601 strings or Unix epoch integers/floats.
    Falls back to ``datetime.now(UTC)`` if parsing fails.
    """
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError):
            return datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            pass
        # Try as a Unix timestamp string
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            pass
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core ingestion logic
# ---------------------------------------------------------------------------


async def _get_or_create_wallet(
    db: AsyncSession,
    address: str,
    wallet_cache: dict[str, Wallet],
) -> Wallet:
    """Retrieve a wallet from the cache/DB or create a new one."""
    addr = address.lower()
    if addr in wallet_cache:
        return wallet_cache[addr]

    # Check database first
    result = await db.execute(select(Wallet).where(Wallet.address == addr))
    wallet = result.scalar_one_or_none()

    if wallet is None:
        wallet = Wallet(
            address=addr,
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

    wallet_cache[addr] = wallet
    return wallet


def _compute_wallet_win_loss(
    trades_for_wallet: list[dict[str, Any]],
    resolution: str,
) -> tuple[int, int]:
    """Determine if the wallet won or lost in this resolved market.

    Returns (1, 0) for a win or (0, 1) for a loss.  A market counts as
    a single bet — the wallet's net position determines the outcome,
    not the number of individual trades.
    """
    resolved_yes = resolution.lower() in ("yes", "1")

    # Compute net dollar exposure on the winning side
    winning_amount = 0.0
    losing_amount = 0.0

    for t in trades_for_wallet:
        side = (t.get("side", "BUY") or "BUY").upper()
        amount = abs(float(t.get("amount", 0)))
        outcome = str(t.get("outcome", "")).lower()

        bet_on_winner = (
            (outcome in ("yes", "1") and resolved_yes)
            or (outcome in ("no", "0") and not resolved_yes)
        )

        if side == "BUY":
            if bet_on_winner:
                winning_amount += amount
            else:
                losing_amount += amount
        else:  # SELL
            if bet_on_winner:
                winning_amount -= amount
            else:
                losing_amount -= amount

    # Net position: if more $ on the winning side, it's a win
    if winning_amount > losing_amount:
        return 1, 0
    elif losing_amount > 0:
        return 0, 1
    else:
        # No meaningful position (e.g. all sells) — don't count
        return 0, 0


async def _fetch_and_store_holders(
    db: AsyncSession,
    condition_id: str,
    market_id: str,
) -> None:
    """Fetch holder data for a market and store in the database."""
    try:
        raw_holders = await polymarket_client.get_holders(condition_id)
        flat_holders: list[dict] = []
        for item in raw_holders:
            if "holders" in item and isinstance(item["holders"], list):
                for h in item["holders"]:
                    h.setdefault("outcomeIndex", item.get("outcomeIndex"))
                    flat_holders.append(h)
            else:
                flat_holders.append(item)

        count = 0
        for h in flat_holders:
            wallet_addr = (h.get("proxyWallet") or h.get("wallet") or "").lower()
            if not wallet_addr:
                continue
            outcome_idx = h.get("outcomeIndex", 0)
            outcome_str = "Yes" if outcome_idx == 0 else "No" if outcome_idx == 1 else str(outcome_idx)
            holder = MarketHolder(
                market_id=market_id,
                wallet_address=wallet_addr,
                outcome=str(h.get("outcome", outcome_str)),
                amount=float(h.get("amount", 0) or 0),
                value_usd=float(h.get("valueUsd", 0) or h.get("value", 0) or 0),
            )
            db.add(holder)
            count += 1
        if count:
            logger.info("  Stored %d holders for market %s", count, market_id)
    except Exception:
        logger.exception("Error fetching holders for market %s", market_id)


async def _fetch_price_history(
    condition_id: str,
    clob_token_ids_json: str | None,
    resolved_at: datetime | None,
) -> list[dict[str, Any]]:
    """Fetch CLOB price history and return normalized snapshots."""
    if not clob_token_ids_json:
        return []

    try:
        token_ids = json.loads(clob_token_ids_json)
    except (json.JSONDecodeError, TypeError):
        return []

    if not token_ids or not isinstance(token_ids, list):
        return []

    token_id = token_ids[0]

    end_ts = None
    start_ts = None
    if resolved_at:
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        end_ts = int(resolved_at.timestamp())
        start_ts = end_ts - (7 * 24 * 3600)

    try:
        raw_history = await polymarket_client.get_price_history(
            token_id=token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            interval="1h",
            fidelity=60,
        )
    except Exception:
        logger.exception("Error fetching price history for token %s", token_id)
        return []

    snapshots: list[dict[str, Any]] = []
    for point in raw_history:
        ts_val = point.get("t")
        price_val = point.get("p")
        if ts_val is None or price_val is None:
            continue
        try:
            ts = datetime.fromtimestamp(int(ts_val), tz=timezone.utc)
            price = float(price_val)
            snapshots.append({"timestamp": ts, "price": price})
        except (ValueError, OSError, TypeError):
            continue

    return snapshots


async def _store_price_snapshots(
    db: AsyncSession,
    market_id: str,
    token_id: str,
    snapshots: list[dict[str, Any]],
) -> None:
    """Persist price snapshots to the database."""
    for snap in snapshots:
        ps = PriceSnapshot(
            market_id=market_id,
            token_id=token_id,
            timestamp=snap["timestamp"],
            price=snap["price"],
        )
        db.add(ps)


async def ingest_market(
    market_data: dict[str, Any],
    db: AsyncSession,
    wallet_cache: dict[str, Wallet],
    min_profit: float = 0.0,
) -> None:
    """Process a single market: save the market, fetch and save trades,
    analyse wallets, and run the suspicion engine.

    Only wallets with profit >= ``min_profit`` in this market are scored
    and persisted.  Trades for wallets below the threshold are skipped
    entirely to reduce DB size and speed up ingestion.
    """
    mapped = polymarket_client.map_market(market_data)
    market_id = mapped["id"]

    if not market_id:
        logger.warning("Skipping market with empty ID: %s", market_data.get("question", "?"))
        return

    # Skip already-ingested markets
    existing_result = await db.execute(select(Market).where(Market.id == market_id))
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        logger.info("Market %s already exists, skipping", market_id)
        return

    logger.info("Ingesting market: %s", mapped["question"][:80])

    market = Market(
        id=market_id,
        question=mapped["question"],
        slug=mapped["slug"],
        entity=mapped["entity"],
        category=mapped["category"],
        resolution=mapped["resolution"],
        resolved_at=mapped["resolved_at"],
        created_at=mapped["created_at"],
        volume=mapped["volume"],
        liquidity=mapped["liquidity"],
        open_interest=mapped["open_interest"],
        volume_24hr=mapped["volume_24hr"],
        clob_token_ids=mapped["clob_token_ids"],
        end_date=mapped["end_date"],
        is_active=False,
        last_ingested_at=datetime.now(timezone.utc),
        suspicious_wallet_count=0,
        suspicion_score=0.0,
    )
    db.add(market)

    # ---- Fetch trades from the Data API ----------------------------------
    raw_trades: list[dict[str, Any]] = []
    try:
        condition_id = market_data.get("conditionId") or market_id
        raw_trades = await polymarket_client.get_trades(
            condition_id=condition_id, limit=10_000
        )
    except Exception:
        logger.exception("Error fetching trades for market %s", market_id)

    if not raw_trades:
        logger.info("No trades found for market %s", market_id)
        await db.flush()
        return

    logger.info("  Fetched %d trades for market %s", len(raw_trades), market_id)

    # ---- Fetch and store holder data -------------------------------------
    condition_id = market_data.get("conditionId") or market_id
    await _fetch_and_store_holders(db, condition_id, market_id)

    # ---- Fetch price history ---------------------------------------------
    price_snapshots: list[dict[str, Any]] = await _fetch_price_history(
        condition_id=condition_id,
        clob_token_ids_json=mapped["clob_token_ids"],
        resolved_at=mapped["resolved_at"],
    )
    if price_snapshots:
        try:
            token_ids = json.loads(mapped["clob_token_ids"] or "[]")
            token_id = token_ids[0] if token_ids else condition_id
        except (json.JSONDecodeError, TypeError, IndexError):
            token_id = condition_id
        await _store_price_snapshots(db, market_id, token_id, price_snapshots)
        logger.info("  Stored %d price snapshots for market %s", len(price_snapshots), market_id)

    # ---- Parse trades and group by wallet ----------------------------------
    # First pass: parse all trades and compute per-wallet profit so we can
    # skip wallets below min_profit before writing anything to the DB.
    parsed_trades: list[dict[str, Any]] = []  # list of parsed trade dicts
    wallet_trades: dict[str, list[dict[str, Any]]] = {}
    seen_trade_ids: set[str] = set()

    resolution = mapped["resolution"].lower()
    resolved_yes = resolution in ("yes", "1") if resolution in ("yes", "no", "1", "0") else None

    for idx, raw_trade in enumerate(raw_trades):
        trade_id = str(
            raw_trade.get("transactionHash")
            or raw_trade.get("id")
            or f"{market_id}-{idx}"
        )
        if trade_id in seen_trade_ids:
            continue
        seen_trade_ids.add(trade_id)

        wallet_addr = (
            raw_trade.get("proxyWallet")
            or raw_trade.get("taker_address")
            or raw_trade.get("maker_address")
            or ""
        ).lower()
        if not wallet_addr:
            continue

        side = raw_trade.get("side", "BUY").upper()
        outcome = raw_trade.get("outcome", raw_trade.get("asset_id", "Unknown"))
        amount = abs(float(raw_trade.get("size", 0) or raw_trade.get("amount", 0) or 0))
        price = float(raw_trade.get("price", 0) or 0)
        timestamp = _parse_ts(raw_trade.get("timestamp"))

        # Estimate profit for resolved markets
        profit: float | None = None
        if resolved_yes is not None:
            if side == "BUY":
                if (outcome.lower() in ("yes", "1") and resolved_yes) or (
                    outcome.lower() in ("no", "0") and not resolved_yes
                ):
                    profit = amount * (1.0 - price)
                else:
                    profit = -amount * price
            else:
                if (outcome.lower() in ("yes", "1") and resolved_yes) or (
                    outcome.lower() in ("no", "0") and not resolved_yes
                ):
                    profit = -amount * (1.0 - price)
                else:
                    profit = amount * price

        trade_dict = {
            "trade_id": trade_id,
            "wallet_addr": wallet_addr,
            "side": side,
            "outcome": str(outcome),
            "amount": amount,
            "price": price,
            "profit": profit or 0.0,
            "timestamp": timestamp,
        }
        parsed_trades.append(trade_dict)
        wallet_trades.setdefault(wallet_addr, []).append(trade_dict)

    # ---- Filter wallets by min_profit ------------------------------------
    wallet_profit: dict[str, float] = {}
    for addr, trades in wallet_trades.items():
        wallet_profit[addr] = sum(t["profit"] for t in trades)

    if min_profit > 0:
        qualified_wallets = {
            addr for addr, prof in wallet_profit.items() if prof >= min_profit
        }
        skipped = len(wallet_trades) - len(qualified_wallets)
        logger.info(
            "  Profit filter ($%.0f): %d wallets qualify, %d skipped",
            min_profit, len(qualified_wallets), skipped,
        )
    else:
        qualified_wallets = set(wallet_trades.keys())

    # ---- Save trades only for qualifying wallets -------------------------
    for td in parsed_trades:
        if td["wallet_addr"] not in qualified_wallets:
            continue
        trade = Trade(
            id=td["trade_id"],
            market_id=market_id,
            wallet_address=td["wallet_addr"],
            side=td["side"],
            outcome=td["outcome"],
            amount=td["amount"],
            price=td["price"],
            profit=td["profit"] if td["profit"] != 0.0 else None,
            timestamp=td["timestamp"],
            is_suspicious=False,
        )
        db.add(trade)

    await db.flush()

    # ---- Compute market-level statistical baselines ----------------------
    all_trade_dicts = [t for t in parsed_trades if t["wallet_addr"] in qualified_wallets]
    market_stats = SuspicionEngine._compute_market_stats(all_trade_dicts)
    logger.info(
        "  Market stats: mean=$%.2f, std=$%.2f, median=$%.2f, p95=$%.2f (%d trades)",
        market_stats.mean, market_stats.std, market_stats.median,
        market_stats.p95, market_stats.count,
    )

    # ---- Score each wallet in this market --------------------------------
    suspicious_count = 0
    score_sum = 0.0

    for wallet_addr in qualified_wallets:
        trades_for_wallet = wallet_trades[wallet_addr]
        try:
            wallet = await _get_or_create_wallet(db, wallet_addr, wallet_cache)

            # Count distinct markets
            market_count_result = await db.execute(
                select(Trade.market_id)
                .where(Trade.wallet_address == wallet_addr)
                .distinct()
            )
            wallet_market_count = len(market_count_result.all())

            # Derive wallet first_seen from earliest trade if not set
            if wallet.first_seen is None:
                earliest_result = await db.execute(
                    select(func.min(Trade.timestamp))
                    .where(Trade.wallet_address == wallet_addr)
                )
                earliest_ts = earliest_result.scalar_one_or_none()
                if earliest_ts is not None:
                    wallet.first_seen = earliest_ts

            # Update wallet stats
            wallet.market_count = wallet_market_count
            total_vol = sum(t["amount"] for t in trades_for_wallet)
            total_prof = sum(t["profit"] for t in trades_for_wallet)
            wallet.total_volume += total_vol
            wallet.total_profit += total_prof

            # Track win/loss
            resolution_str = mapped["resolution"]
            wins, losses = _compute_wallet_win_loss(trades_for_wallet, resolution_str)
            wallet.win_count += wins
            wallet.loss_count += losses
            total_markets = wallet.win_count + wallet.loss_count
            wallet.win_rate = (
                wallet.win_count / total_markets if total_markets > 0 else 0.0
            )

            # Run the suspicion engine
            score, reasons = suspicion_engine.score_wallet(
                wallet_address=wallet_addr,
                market_id=market_id,
                trades=trades_for_wallet,
                market_volume=mapped["volume"],
                resolution=resolution_str,
                win_rate=wallet.win_rate,
                total_markets=total_markets,
            )

            # Threshold: create a flag if score >= 0.3
            if score >= 0.3:
                flag = SuspicionFlag(
                    wallet_address=wallet_addr,
                    market_id=market_id,
                    score=score,
                    reasons=json.dumps(reasons),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(flag)
                suspicious_count += 1

                # Mark this wallet's trades as suspicious
                trade_update_stmt = (
                    select(Trade)
                    .where(Trade.market_id == market_id)
                    .where(Trade.wallet_address == wallet_addr)
                )
                trade_update_result = await db.execute(trade_update_stmt)
                for t in trade_update_result.scalars().all():
                    t.is_suspicious = True

            # Update wallet-level suspicion score (running max)
            wallet.suspicion_score = max(wallet.suspicion_score, score)
            score_sum += score

        except Exception:
            logger.exception(
                "Error scoring wallet %s in market %s", wallet_addr, market_id
            )

    # Update market-level aggregates
    market.suspicious_wallet_count = suspicious_count
    if wallet_trades:
        market.suspicion_score = round(score_sum / len(wallet_trades), 4)
    else:
        market.suspicion_score = 0.0

    await db.flush()
    logger.info(
        "  Market %s: %d wallets, %d suspicious (score=%.4f)",
        market_id,
        len(wallet_trades),
        suspicious_count,
        market.suspicion_score,
    )


async def ingest_markets(limit: int = 20, min_profit: float = 0.0) -> None:
    """Main ingestion entry point.

    Fetches closed events from the Gamma API and processes each market.
    Only wallets with profit >= ``min_profit`` per market are tracked.
    """
    await init_db()
    logger.info("Starting ingestion of up to %d markets", limit)

    progress["running"] = True
    progress["current"] = 0
    progress["total"] = 0
    progress["current_market"] = "Fetching events..."
    progress["markets_done"] = []
    progress["error"] = None

    wallet_cache: dict[str, Wallet] = {}

    async with async_session() as db:
        try:
            events = await polymarket_client.get_events(limit=100, closed=True)
            events.sort(key=lambda e: float(e.get("volume", 0) or 0), reverse=True)
            logger.info("Fetched %d events from Gamma API", len(events))

            all_markets: list[dict[str, Any]] = []
            for event in events:
                for m in event.get("markets", []):
                    m["_event_title"] = event.get("title", "Unknown")
                    all_markets.append(m)

            all_markets.sort(
                key=lambda m: float(m.get("volumeNum", 0) or m.get("volume", 0) or 0),
                reverse=True,
            )
            all_markets = all_markets[:limit]

            total = len(all_markets)
            progress["total"] = total

            for i, market_data in enumerate(all_markets, 1):
                question = market_data.get("question", "?")[:70]
                progress["current"] = i
                progress["current_market"] = question
                try:
                    logger.info("[%d/%d] %s", i, total, question)
                    await ingest_market(market_data, db, wallet_cache, min_profit=min_profit)
                    progress["markets_done"].append(question)
                except Exception:
                    logger.exception("Failed: %s", question)
                    continue

            await db.commit()
            logger.info("Ingestion complete.")
            progress["current_market"] = "Done!"

        except Exception as exc:
            logger.exception("Fatal error during ingestion")
            progress["error"] = str(exc)
            await db.rollback()
            raise
        finally:
            progress["running"] = False


if __name__ == "__main__":
    limit = 20
    if "--limit" in sys.argv:
        try:
            idx = sys.argv.index("--limit")
            limit = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    asyncio.run(ingest_markets(limit=limit))
