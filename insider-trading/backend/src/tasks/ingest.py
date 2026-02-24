"""Data ingestion script for the Polymarket Insider Trading Detector.

Fetches resolved markets from Polymarket's Gamma API, retrieves trades
from the CLOB API, analyses wallets via PolygonScan, scores each
wallet-market pair through the suspicion engine, and persists everything
to the database.

Phase 2 additions:
- Temporal cluster detection (C1)
- Funding source clustering (C2)
- Win rate tracking (D1)
- Market-level statistical baselines (D2)
- Volume-weighted timing with escalation (D3)
- Holder data fetching (E1)
- CLOB price history + spike detection (E2)
- Wallet activity for high-scorers (E3)

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
from src.services.blockchain import blockchain_client
from src.services.polymarket import polymarket_client
from src.services.suspicion import SuspicionEngine, suspicion_engine
from src.services.temporal_cluster import TemporalClusterDetector
from src.services.wallet_cluster import FundingClusterDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Module-level detector instances
_temporal_detector = TemporalClusterDetector()
_funding_detector = FundingClusterDetector()

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
    """Retrieve a wallet from the cache/DB or create a new one.

    On first encounter, queries PolygonScan for the wallet creation date
    and funding source.  Results are cached for the duration of the run.
    """
    addr = address.lower()
    if addr in wallet_cache:
        return wallet_cache[addr]

    # Check database first
    result = await db.execute(select(Wallet).where(Wallet.address == addr))
    wallet = result.scalar_one_or_none()

    if wallet is None:
        # Create wallet without PolygonScan lookup.
        # Blockchain enrichment (first_seen, funding_source) happens in a
        # separate post-scoring pass for high-scoring wallets only, to
        # avoid 6000+ API calls per market that would take 20+ minutes.
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
    """Determine wins/losses from a wallet's trades in a resolved market.

    Returns (wins, losses) where a 'win' means the wallet's BUY trade
    was on the side that ultimately resolved correctly.
    """
    resolved_yes = resolution.lower() in ("yes", "1")
    wins = 0
    losses = 0

    for t in trades_for_wallet:
        side = (t.get("side", "BUY") or "BUY").upper()
        if side != "BUY":
            continue
        outcome = str(t.get("outcome", "")).lower()
        bet_on_winner = (
            (outcome in ("yes", "1") and resolved_yes)
            or (outcome in ("no", "0") and not resolved_yes)
        )
        if bet_on_winner:
            wins += 1
        else:
            losses += 1

    return wins, losses


async def _fetch_and_store_holders(
    db: AsyncSession,
    condition_id: str,
    market_id: str,
) -> None:
    """Fetch holder data for a market and store in the database (E1)."""
    try:
        raw_holders = await polymarket_client.get_holders(condition_id)
        # API may return nested format: [{token, holders: [...]}, ...]
        # or flat format: [{proxyWallet, amount, ...}, ...]
        flat_holders: list[dict] = []
        for item in raw_holders:
            if "holders" in item and isinstance(item["holders"], list):
                # Nested format — flatten, carrying outcome index from parent
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
    """Fetch CLOB price history and return normalized snapshots (E2).

    Returns a list of dicts with ``timestamp`` (datetime) and ``price`` (float).
    """
    if not clob_token_ids_json:
        return []

    try:
        token_ids = json.loads(clob_token_ids_json)
    except (json.JSONDecodeError, TypeError):
        return []

    if not token_ids or not isinstance(token_ids, list):
        return []

    # Use the first token ID (Yes outcome) for price history
    token_id = token_ids[0]

    # Compute time range: up to 7 days before resolution
    end_ts = None
    start_ts = None
    if resolved_at:
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        end_ts = int(resolved_at.timestamp())
        start_ts = end_ts - (7 * 24 * 3600)  # 7 days before

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
    """Persist price snapshots to the database (E2)."""
    for snap in snapshots:
        ps = PriceSnapshot(
            market_id=market_id,
            token_id=token_id,
            timestamp=snap["timestamp"],
            price=snap["price"],
        )
        db.add(ps)


async def _enrich_wallet_market_count(
    wallet_addr: str,
    current_count: int,
) -> int:
    """Use the wallet activity endpoint to get a better cross-market count (E3).

    Only called for wallets with score >= 0.2 to avoid rate limits.
    Returns the updated market count.
    """
    try:
        activities = await polymarket_client.get_wallet_activity(
            wallet_address=wallet_addr,
            limit=100,
            activity_types=["TRADE"],
        )
        if activities:
            # Count distinct market/condition IDs from activity
            market_ids = set()
            for act in activities:
                mid = act.get("conditionId") or act.get("market") or act.get("slug")
                if mid:
                    market_ids.add(mid)
            if len(market_ids) > current_count:
                return len(market_ids)
    except Exception:
        logger.debug("Could not fetch activity for wallet %s", wallet_addr)
    return current_count


async def ingest_market(
    market_data: dict[str, Any],
    db: AsyncSession,
    wallet_cache: dict[str, Wallet],
) -> None:
    """Process a single market: save the market, fetch and save trades,
    analyse wallets, and run the suspicion engine.

    Args:
        market_data: Raw market dict from the Gamma API.
        db: Active database session (caller is responsible for committing).
        wallet_cache: Shared wallet cache across the ingestion run.
    """
    # Map raw API data to our model fields
    mapped = polymarket_client.map_market(market_data)
    market_id = mapped["id"]

    if not market_id:
        logger.warning("Skipping market with empty ID: %s", market_data.get("question", "?"))
        return

    # Check if market already ingested
    existing = await db.execute(select(Market).where(Market.id == market_id))
    if existing.scalar_one_or_none() is not None:
        logger.info("Market %s already exists, skipping", market_id)
        return

    logger.info("Ingesting market: %s", mapped["question"][:80])

    # Create Market record
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
        # Still save the market, just with no trades
        await db.flush()
        return

    logger.info("  Fetched %d trades for market %s", len(raw_trades), market_id)

    # ---- E1: Fetch and store holder data ---------------------------------
    condition_id = market_data.get("conditionId") or market_id
    await _fetch_and_store_holders(db, condition_id, market_id)

    # ---- E2: Fetch price history -----------------------------------------
    price_snapshots: list[dict[str, Any]] = await _fetch_price_history(
        condition_id=condition_id,
        clob_token_ids_json=mapped["clob_token_ids"],
        resolved_at=mapped["resolved_at"],
    )
    if price_snapshots:
        # Store snapshots in DB
        try:
            token_ids = json.loads(mapped["clob_token_ids"] or "[]")
            token_id = token_ids[0] if token_ids else condition_id
        except (json.JSONDecodeError, TypeError, IndexError):
            token_id = condition_id
        await _store_price_snapshots(db, market_id, token_id, price_snapshots)
        logger.info("  Stored %d price snapshots for market %s", len(price_snapshots), market_id)

    # ---- Save trades and collect wallet addresses ------------------------
    # Group trades by wallet address for later scoring
    wallet_trades: dict[str, list[dict[str, Any]]] = {}
    all_trade_dicts: list[dict[str, Any]] = []  # flat list for market stats (D2)
    seen_trade_ids: set[str] = set()

    for idx, raw_trade in enumerate(raw_trades):
        # Data API uses transactionHash as unique ID
        trade_id = str(
            raw_trade.get("transactionHash")
            or raw_trade.get("id")
            or f"{market_id}-{idx}"
        )
        if trade_id in seen_trade_ids:
            continue
        seen_trade_ids.add(trade_id)

        # Data API uses proxyWallet for the wallet address
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

        # Estimate profit: for resolved markets we can compute it
        profit: float | None = None
        resolution = mapped["resolution"].lower()
        if resolution in ("yes", "no", "1", "0"):
            resolved_yes = resolution in ("yes", "1")
            if side == "BUY":
                if (outcome.lower() in ("yes", "1") and resolved_yes) or (
                    outcome.lower() in ("no", "0") and not resolved_yes
                ):
                    # Winning trade: paid price, received 1.0
                    profit = amount * (1.0 - price)
                else:
                    # Losing trade: paid price, received 0.0
                    profit = -amount * price
            else:
                # SELL side is the inverse
                if (outcome.lower() in ("yes", "1") and resolved_yes) or (
                    outcome.lower() in ("no", "0") and not resolved_yes
                ):
                    profit = -amount * (1.0 - price)
                else:
                    profit = amount * price

        trade = Trade(
            id=trade_id,
            market_id=market_id,
            wallet_address=wallet_addr,
            side=side,
            outcome=str(outcome),
            amount=amount,
            price=price,
            profit=profit,
            timestamp=timestamp,
            is_suspicious=False,  # will be updated after scoring
        )
        db.add(trade)

        # Collect for scoring
        trade_dict = {
            "amount": amount,
            "price": price,
            "profit": profit or 0.0,
            "timestamp": timestamp,
            "side": side,
            "outcome": str(outcome),
        }
        wallet_trades.setdefault(wallet_addr, []).append(trade_dict)
        all_trade_dicts.append(trade_dict)

    await db.flush()  # ensure trades get IDs

    # ---- D2: Compute market-level statistical baselines ------------------
    market_stats = SuspicionEngine._compute_market_stats(all_trade_dicts)
    logger.info(
        "  Market stats: mean=$%.2f, std=$%.2f, median=$%.2f, p95=$%.2f (%d trades)",
        market_stats.mean, market_stats.std, market_stats.median,
        market_stats.p95, market_stats.count,
    )

    # ---- C1: Temporal cluster detection ----------------------------------
    temporal_clustered = _temporal_detector.get_clustered_wallets(wallet_trades)
    if temporal_clustered:
        logger.info(
            "  Temporal clusters found: %d wallets in clusters",
            len(temporal_clustered),
        )

    # ---- C2: Funding source clustering -----------------------------------
    # Build wallet -> funding_source map for wallets in this market
    wallet_funding: dict[str, str | None] = {}
    for wallet_addr in wallet_trades:
        wallet = await _get_or_create_wallet(db, wallet_addr, wallet_cache)
        wallet_funding[wallet_addr] = wallet.funding_source
    funding_clustered = _funding_detector.get_clustered_wallets(wallet_funding)
    if funding_clustered:
        logger.info(
            "  Funding clusters found: %d wallets in clusters",
            len(funding_clustered),
        )

    # ---- Score each wallet in this market --------------------------------
    suspicious_count = 0
    score_sum = 0.0

    # First pass: score all wallets
    wallet_scores: dict[str, tuple[float, list[str]]] = {}

    for wallet_addr, trades_for_wallet in wallet_trades.items():
        try:
            wallet = await _get_or_create_wallet(db, wallet_addr, wallet_cache)

            # Count how many distinct markets this wallet appears in
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

            # D1: Track win/loss per wallet
            resolution_str = mapped["resolution"]
            wins, losses = _compute_wallet_win_loss(trades_for_wallet, resolution_str)
            wallet.win_count += wins
            wallet.loss_count += losses
            total_bets = wallet.win_count + wallet.loss_count
            wallet.win_rate = (
                wallet.win_count / total_bets if total_bets > 0 else 0.0
            )

            # Run the suspicion engine (D1 win_rate, D2 market_stats,
            # D3 new timing, E2 price snapshots all integrated)
            resolved_at = mapped["resolved_at"] or datetime.now(timezone.utc)
            score, reasons = suspicion_engine.score_wallet(
                wallet_address=wallet_addr,
                market_id=market_id,
                trades=trades_for_wallet,
                market_resolved_at=resolved_at,
                wallet_first_seen=wallet.first_seen,
                wallet_market_count=wallet_market_count,
                market_volume=mapped["volume"],
                resolution=resolution_str,
                win_rate=wallet.win_rate,
                total_bets=total_bets,
                market_stats=market_stats,
                price_snapshots=price_snapshots if price_snapshots else None,
                market_end_date=mapped.get("end_date"),
            )

            wallet_scores[wallet_addr] = (score, reasons)

        except Exception:
            logger.exception(
                "Error scoring wallet %s in market %s", wallet_addr, market_id
            )

    # ---- E3: Enrich market count for wallets scoring >= 0.2 --------------
    # Batch E3 calls with concurrency limit to avoid API rate limits
    wallets_to_enrich = [
        (addr, wallet_cache[addr])
        for addr, (score, _) in wallet_scores.items()
        if score >= 0.2 and addr in wallet_cache
    ]
    logger.info("  E3: enriching %d wallets (score >= 0.2)", len(wallets_to_enrich))

    _e3_semaphore = asyncio.Semaphore(10)  # max 10 concurrent API calls

    async def _enrich_one(addr: str, wallet: Wallet) -> tuple[str, bool]:
        async with _e3_semaphore:
            old_count = wallet.market_count
            new_count = await _enrich_wallet_market_count(addr, old_count)
            if new_count != old_count:
                wallet.market_count = new_count
                return addr, True
            return addr, False

    enrichment_results = await asyncio.gather(
        *[_enrich_one(addr, w) for addr, w in wallets_to_enrich],
        return_exceptions=True,
    )
    wallets_to_rescore: list[str] = [
        addr for result in enrichment_results
        if not isinstance(result, Exception) and result[1]
        for addr in [result[0]]
    ]
    logger.info("  E3: %d wallets got updated market counts", len(wallets_to_rescore))

    # Re-score wallets that got enriched market counts
    for wallet_addr in wallets_to_rescore:
        try:
            wallet = wallet_cache[wallet_addr]
            trades_for_wallet = wallet_trades[wallet_addr]
            resolved_at = mapped["resolved_at"] or datetime.now(timezone.utc)
            total_bets = wallet.win_count + wallet.loss_count
            score, reasons = suspicion_engine.score_wallet(
                wallet_address=wallet_addr,
                market_id=market_id,
                trades=trades_for_wallet,
                market_resolved_at=resolved_at,
                wallet_first_seen=wallet.first_seen,
                wallet_market_count=wallet.market_count,
                market_volume=mapped["volume"],
                resolution=mapped["resolution"],
                win_rate=wallet.win_rate,
                total_bets=total_bets,
                market_stats=market_stats,
                price_snapshots=price_snapshots if price_snapshots else None,
                market_end_date=mapped.get("end_date"),
            )
            wallet_scores[wallet_addr] = (score, reasons)
        except Exception:
            logger.exception(
                "Error re-scoring wallet %s after enrichment", wallet_addr
            )

    # ---- A3: PolygonScan enrichment for high-scoring wallets ---------------
    # Only look up blockchain data for wallets scoring >= 0.3 to limit API calls.
    wallets_for_blockchain = [
        (addr, wallet_cache[addr])
        for addr, (score, _) in wallet_scores.items()
        if score >= 0.3 and addr in wallet_cache
        and wallet_cache[addr].first_seen is None
        and wallet_cache[addr].funding_source is None
    ]
    if wallets_for_blockchain:
        blockchain_client.reset()
        logger.info("  A3: enriching %d wallets via PolygonScan (score >= 0.3)", len(wallets_for_blockchain))

        async def _blockchain_enrich_one(addr: str, wallet: Wallet) -> None:
            try:
                first_seen, funding_source = await asyncio.gather(
                    blockchain_client.get_wallet_creation_date(addr),
                    blockchain_client.get_funding_source(addr),
                )
                if first_seen is not None:
                    wallet.first_seen = first_seen
                if funding_source is not None:
                    wallet.funding_source = funding_source
            except Exception:
                pass

        await asyncio.gather(
            *[_blockchain_enrich_one(addr, w) for addr, w in wallets_for_blockchain],
            return_exceptions=True,
        )
        enriched_count = sum(
            1 for _, w in wallets_for_blockchain
            if w.first_seen is not None or w.funding_source is not None
        )
        logger.info("  A3: %d wallets enriched with blockchain data", enriched_count)

    # Rebuild funding map for C2 now that we have blockchain data
    for wallet_addr in wallet_trades:
        wallet = wallet_cache.get(wallet_addr)
        if wallet and wallet.funding_source:
            wallet_funding[wallet_addr] = wallet.funding_source
    funding_clustered = _funding_detector.get_clustered_wallets(wallet_funding)

    # ---- Apply cluster boosts (C1, C2) and persist flags -----------------
    CLUSTER_BOOST = 0.15

    for wallet_addr, (score, reasons) in wallet_scores.items():
        try:
            wallet = wallet_cache.get(wallet_addr)
            if wallet is None:
                continue

            # C1: Temporal cluster boost
            if wallet_addr in temporal_clustered:
                clusters = temporal_clustered[wallet_addr]
                for cluster in clusters:
                    score = min(1.0, score + CLUSTER_BOOST)
                    reasons.append(
                        f"Temporal cluster: {cluster.size} wallets {cluster.side} "
                        f"{cluster.outcome} within {cluster.window_hours}h window"
                    )

            # C2: Funding source cluster boost
            if wallet_addr in funding_clustered:
                f_clusters = funding_clustered[wallet_addr]
                for f_cluster in f_clusters:
                    score = min(1.0, score + CLUSTER_BOOST)
                    reasons.append(
                        f"Funding cluster: {f_cluster.size} wallets share funding "
                        f"source {f_cluster.funding_source[:10]}..."
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

                # Mark this wallet's trades in this market as suspicious
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
                "Error applying cluster boosts for wallet %s in market %s",
                wallet_addr, market_id,
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


async def ingest_markets(limit: int = 20) -> None:
    """Main ingestion entry point.

    Fetches resolved events from the Gamma API and processes each market.

    Args:
        limit: Maximum number of events to fetch.
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
                    await ingest_market(market_data, db, wallet_cache)
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
    # Support --limit N from the command line
    limit = 20
    if "--limit" in sys.argv:
        try:
            idx = sys.argv.index("--limit")
            limit = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    asyncio.run(ingest_markets(limit=limit))
