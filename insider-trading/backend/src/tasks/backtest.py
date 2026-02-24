"""Backtest framework for calibrating the suspicion engine (F1).

Scores known insider wallets from documented Polymarket cases against the
suspicion engine to validate that:
  1. Known insiders score above the flagging threshold (0.3)
  2. Random/normal wallets score below the threshold (low false positive rate)

Known cases:
  - Maduro Venezuelan election market (coordinated wallet ring)
  - Various documented high-profile Polymarket insider trading incidents

Usage:
    python -m src.tasks.backtest                # run all cases
    python -m src.tasks.backtest --verbose      # detailed output
    python -m src.tasks.backtest --case maduro   # run a specific case
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.services.polymarket import polymarket_client
from src.services.suspicion import SuspicionEngine, suspicion_engine
from src.services.temporal_cluster import TemporalClusterDetector
from src.services.wallet_cluster import FundingClusterDetector
from src.tasks.ingest import _compute_wallet_win_loss, _parse_ts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

FLAGGING_THRESHOLD = 0.3
CLUSTER_BOOST = 0.15


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------


@dataclass
class BacktestWallet:
    """A wallet to evaluate in the backtest."""

    address: str
    label: str
    is_insider: bool  # True = known insider, False = expected normal


@dataclass
class BacktestCase:
    """A market + set of wallets to backtest."""

    name: str
    description: str
    condition_id: str
    resolution: str  # "Yes" or "No"
    slug: str = ""  # Slug for reliable market lookup (Gamma API condition_id filter is broken)
    wallets: list[BacktestWallet] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Result of scoring a single wallet in a backtest case."""

    case_name: str
    wallet_address: str
    wallet_label: str
    is_insider: bool
    score: float
    reasons: list[str]
    flagged: bool  # score >= FLAGGING_THRESHOLD
    correct: bool  # flagged == is_insider


# ---------------------------------------------------------------------------
# Known cases — add more as they are documented
# ---------------------------------------------------------------------------

# NOTE: These are proxy wallet addresses from documented public cases.
# The condition IDs correspond to specific Polymarket markets.

BACKTEST_CASES: list[BacktestCase] = [
    BacktestCase(
        name="trump_2024_election",
        description=(
            "2024 US Presidential Election — the highest-volume Polymarket "
            "market. Should have a mix of normal traders and potentially "
            "suspicious activity patterns."
        ),
        condition_id="0xdd22472e552920b8438158ea7238bfadfa4f736aa4cee91a6b86c39ead110917",
        resolution="Yes",
        slug="will-donald-trump-win-the-2024-us-presidential-election",
        wallets=[
            # Large known whale accounts (Théo / Fredi) — public, not
            # necessarily insiders but very large positions. These test
            # that large position size alone doesn't guarantee a flag
            # without other corroborating signals.
            BacktestWallet(
                address="0x25e940685f5e3a2e90dc50fceda0bba56924011b",
                label="Large public whale (expected normal or borderline)",
                is_insider=False,
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------


async def _fetch_market_data(
    condition_id: str,
    slug: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Fetch market metadata and trades for a backtest case.

    Tries ``get_market(condition_id)`` first; if the Gamma API's broken
    filter returns nothing, falls back to ``get_market_by_slug(slug)``.
    """
    market_data = await polymarket_client.get_market(condition_id)
    if not market_data and slug:
        logger.info("condition_id lookup failed, falling back to slug: %s", slug)
        market_data = await polymarket_client.get_market_by_slug(slug)
    if not market_data:
        logger.error("Could not fetch market data for %s", condition_id)
        return None, []

    trades = await polymarket_client.get_trades(condition_id, limit=10_000)
    logger.info(
        "Fetched %d trades for market %s",
        len(trades),
        market_data.get("question", condition_id)[:60],
    )
    return market_data, trades


def _group_trades_by_wallet(
    raw_trades: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group raw API trades into per-wallet trade dicts for scoring."""
    wallet_trades: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()

    for idx, raw in enumerate(raw_trades):
        trade_id = str(raw.get("transactionHash") or raw.get("id") or f"bt-{idx}")
        if trade_id in seen:
            continue
        seen.add(trade_id)

        wallet_addr = (
            raw.get("proxyWallet")
            or raw.get("taker_address")
            or raw.get("maker_address")
            or ""
        ).lower()
        if not wallet_addr:
            continue

        side = (raw.get("side") or "BUY").upper()
        outcome = str(raw.get("outcome") or raw.get("asset_id") or "Unknown")
        amount = abs(float(raw.get("size") or raw.get("amount") or 0))
        price = float(raw.get("price") or 0)
        timestamp = _parse_ts(raw.get("timestamp"))

        trade_dict = {
            "amount": amount,
            "price": price,
            "profit": 0.0,  # will compute below
            "timestamp": timestamp,
            "side": side,
            "outcome": outcome,
        }
        wallet_trades.setdefault(wallet_addr, []).append(trade_dict)

    return wallet_trades


def _compute_profits(
    wallet_trades: dict[str, list[dict[str, Any]]],
    resolution: str,
) -> None:
    """Compute profit for each trade in place based on market resolution."""
    resolved_yes = resolution.lower() in ("yes", "1")

    for trades in wallet_trades.values():
        for t in trades:
            side = t["side"]
            outcome = t["outcome"].lower()
            amount = t["amount"]
            price = t["price"]

            if side == "BUY":
                if (outcome in ("yes", "1") and resolved_yes) or (
                    outcome in ("no", "0") and not resolved_yes
                ):
                    t["profit"] = amount * (1.0 - price)
                else:
                    t["profit"] = -amount * price
            else:
                if (outcome in ("yes", "1") and resolved_yes) or (
                    outcome in ("no", "0") and not resolved_yes
                ):
                    t["profit"] = -amount * (1.0 - price)
                else:
                    t["profit"] = amount * price


async def run_backtest_case(
    case: BacktestCase,
    verbose: bool = False,
) -> list[BacktestResult]:
    """Run the backtest for a single case (market + wallets)."""
    logger.info("=" * 60)
    logger.info("BACKTEST: %s", case.name)
    logger.info("  %s", case.description)
    logger.info("=" * 60)

    market_data, raw_trades = await _fetch_market_data(case.condition_id, slug=case.slug)
    if not market_data or not raw_trades:
        logger.error("Skipping case %s — no data", case.name)
        return []

    mapped = polymarket_client.map_market(market_data)
    market_volume = mapped["volume"]
    resolved_at = mapped["resolved_at"] or datetime.now(timezone.utc)
    end_date = mapped.get("end_date")

    # Group and compute profits
    wallet_trades = _group_trades_by_wallet(raw_trades)
    _compute_profits(wallet_trades, case.resolution)

    # Compute market stats (D2)
    all_trade_dicts = [t for trades in wallet_trades.values() for t in trades]
    market_stats = SuspicionEngine._compute_market_stats(all_trade_dicts)
    logger.info(
        "  Market stats: mean=$%.2f, std=$%.2f, count=%d",
        market_stats.mean,
        market_stats.std,
        market_stats.count,
    )

    # Temporal clusters (C1)
    temporal_detector = TemporalClusterDetector()
    temporal_clustered = temporal_detector.get_clustered_wallets(wallet_trades)

    # Fetch price history (E2)
    price_snapshots: list[dict[str, Any]] = []
    clob_token_ids = mapped.get("clob_token_ids")
    if clob_token_ids:
        try:
            token_ids = json.loads(clob_token_ids)
            if token_ids:
                end_ts = int(resolved_at.timestamp()) if resolved_at.tzinfo else None
                start_ts = (end_ts - 7 * 24 * 3600) if end_ts else None
                raw_history = await polymarket_client.get_price_history(
                    token_id=token_ids[0],
                    start_ts=start_ts,
                    end_ts=end_ts,
                    interval="1h",
                    fidelity=60,
                )
                for point in raw_history:
                    ts_val = point.get("t")
                    price_val = point.get("p")
                    if ts_val is not None and price_val is not None:
                        try:
                            ts = datetime.fromtimestamp(int(ts_val), tz=timezone.utc)
                            price_snapshots.append(
                                {"timestamp": ts, "price": float(price_val)}
                            )
                        except (ValueError, OSError):
                            pass
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

    if price_snapshots:
        logger.info("  Fetched %d price snapshots", len(price_snapshots))

    # Score each target wallet
    results: list[BacktestResult] = []

    # Also score a random sample of non-target wallets for false positive analysis
    target_addrs = {w.address.lower() for w in case.wallets}
    non_target_addrs = [
        addr for addr in wallet_trades if addr not in target_addrs
    ]

    # Take up to 50 random non-target wallets for false positive rate estimation
    import random
    sample_size = min(50, len(non_target_addrs))
    sampled_normals = random.sample(non_target_addrs, sample_size) if non_target_addrs else []

    # Combine target wallets + sampled normals
    all_test_wallets: list[BacktestWallet] = list(case.wallets)
    for addr in sampled_normals:
        all_test_wallets.append(
            BacktestWallet(address=addr, label="random_sample", is_insider=False)
        )

    for test_wallet in all_test_wallets:
        addr = test_wallet.address.lower()
        trades = wallet_trades.get(addr)
        if not trades:
            logger.warning(
                "  Wallet %s (%s) has no trades in this market — skipping",
                addr[:10],
                test_wallet.label,
            )
            continue

        # Compute wallet-level stats
        wallet_market_count = 1  # We only have this market's data in backtest
        wallet_first_seen = min(
            (t["timestamp"] for t in trades if isinstance(t.get("timestamp"), datetime)),
            default=None,
        )

        # Win rate across this market only (D1)
        wins, losses = _compute_wallet_win_loss(trades, case.resolution)
        total_bets = wins + losses
        win_rate = wins / total_bets if total_bets > 0 else 0.0

        # Try to enrich market count and first_seen via activity API (E3)
        try:
            activities = await polymarket_client.get_wallet_activity(
                wallet_address=addr, limit=100, activity_types=["TRADE"]
            )
            if activities:
                market_ids = set()
                earliest_activity: datetime | None = None
                for act in activities:
                    mid = act.get("conditionId") or act.get("market") or act.get("slug")
                    if mid:
                        market_ids.add(mid)
                    # Track earliest activity timestamp
                    act_ts = act.get("timestamp") or act.get("createdAt")
                    if act_ts:
                        try:
                            parsed = _parse_ts(act_ts)
                            if parsed and (earliest_activity is None or parsed < earliest_activity):
                                earliest_activity = parsed
                        except Exception:
                            pass
                if len(market_ids) > wallet_market_count:
                    wallet_market_count = len(market_ids)
                if earliest_activity and (wallet_first_seen is None or earliest_activity < wallet_first_seen):
                    wallet_first_seen = earliest_activity
        except Exception:
            pass

        # If we have unreliable wallet_first_seen data (derived from trades
        # in this market only, and E3 activity is all post-resolution), but
        # the wallet has many markets, pass None for moderate-risk default
        # instead of a misleading "0 days" value.
        if (
            wallet_market_count > 5
            and wallet_first_seen is not None
            and resolved_at
            and (resolved_at - wallet_first_seen).total_seconds() < 7 * 86_400
        ):
            wallet_first_seen = None  # Unreliable — default to 0.5

        # Score via suspicion engine
        score, reasons = suspicion_engine.score_wallet(
            wallet_address=addr,
            market_id=case.condition_id,
            trades=trades,
            market_resolved_at=resolved_at,
            wallet_first_seen=wallet_first_seen,
            wallet_market_count=wallet_market_count,
            market_volume=market_volume,
            resolution=case.resolution,
            win_rate=win_rate,
            total_bets=total_bets,
            market_stats=market_stats,
            price_snapshots=price_snapshots if price_snapshots else None,
            market_end_date=end_date,
        )

        # Apply cluster boosts (C1) — only for small, coordinated clusters
        # Large clusters (>20% of all trading wallets) are normal market
        # activity on high-volume markets, not suspicious coordination.
        total_wallets_in_market = len(wallet_trades)
        if addr in temporal_clustered:
            for cluster in temporal_clustered[addr]:
                cluster_ratio = cluster.size / max(total_wallets_in_market, 1)
                if cluster_ratio <= 0.20 and cluster.size >= 3:
                    score = min(1.0, score + CLUSTER_BOOST)
                    reasons.append(
                        f"Temporal cluster: {cluster.size} wallets {cluster.side} "
                        f"{cluster.outcome} within {cluster.window_hours}h"
                    )
                else:
                    reasons.append(
                        f"Large temporal group: {cluster.size}/{total_wallets_in_market} "
                        f"wallets ({cluster_ratio:.0%}) — normal market activity, no boost"
                    )

        flagged = score >= FLAGGING_THRESHOLD
        correct = flagged == test_wallet.is_insider

        result = BacktestResult(
            case_name=case.name,
            wallet_address=addr,
            wallet_label=test_wallet.label,
            is_insider=test_wallet.is_insider,
            score=score,
            reasons=reasons,
            flagged=flagged,
            correct=correct,
        )
        results.append(result)

        if verbose or test_wallet.label != "random_sample":
            status = "CORRECT" if correct else "WRONG"
            flag_str = "FLAGGED" if flagged else "clear"
            insider_str = "INSIDER" if test_wallet.is_insider else "normal"
            logger.info(
                "  [%s] %s (%s) — score=%.3f %s (expected: %s)",
                status,
                addr[:16] + "...",
                test_wallet.label[:30],
                score,
                flag_str,
                insider_str,
            )
            if verbose:
                for reason in reasons:
                    logger.info("    - %s", reason)

    return results


# ---------------------------------------------------------------------------
# Summary reporting
# ---------------------------------------------------------------------------


def print_summary(all_results: list[BacktestResult]) -> None:
    """Print a summary of backtest results."""
    if not all_results:
        logger.info("No results to summarize.")
        return

    # Separate insiders from normals
    insiders = [r for r in all_results if r.is_insider]
    normals = [r for r in all_results if not r.is_insider]

    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)

    # True positive rate (sensitivity)
    if insiders:
        tp = sum(1 for r in insiders if r.flagged)
        fn = sum(1 for r in insiders if not r.flagged)
        sensitivity = tp / len(insiders) if insiders else 0
        print(f"\nKnown Insiders: {len(insiders)}")
        print(f"  True Positives (correctly flagged):  {tp}")
        print(f"  False Negatives (missed):            {fn}")
        print(f"  Sensitivity (recall):                {sensitivity:.1%}")
        print(f"  Avg score:                           {sum(r.score for r in insiders) / len(insiders):.3f}")

        if fn > 0:
            print("\n  MISSED insiders:")
            for r in insiders:
                if not r.flagged:
                    print(f"    {r.wallet_address[:16]}... score={r.score:.3f} ({r.wallet_label})")
    else:
        print("\nNo known insider wallets in test cases.")

    # False positive rate (specificity)
    if normals:
        fp = sum(1 for r in normals if r.flagged)
        tn = sum(1 for r in normals if not r.flagged)
        specificity = tn / len(normals) if normals else 0
        fpr = fp / len(normals) if normals else 0
        print(f"\nNormal Wallets (sample): {len(normals)}")
        print(f"  True Negatives (correctly cleared):  {tn}")
        print(f"  False Positives (wrongly flagged):    {fp}")
        print(f"  Specificity:                         {specificity:.1%}")
        print(f"  False Positive Rate:                 {fpr:.1%}")
        print(f"  Avg score:                           {sum(r.score for r in normals) / len(normals):.3f}")

        # Show score distribution
        scores = sorted([r.score for r in normals])
        if scores:
            print(f"\n  Score distribution (normals):")
            print(f"    Min:    {scores[0]:.3f}")
            print(f"    25th:   {scores[len(scores) // 4]:.3f}")
            print(f"    Median: {scores[len(scores) // 2]:.3f}")
            print(f"    75th:   {scores[3 * len(scores) // 4]:.3f}")
            print(f"    Max:    {scores[-1]:.3f}")

        # Show the highest-scoring false positives for investigation
        if fp > 0:
            print(f"\n  Top false positives (score >= {FLAGGING_THRESHOLD}):")
            flagged_normals = sorted(
                [r for r in normals if r.flagged],
                key=lambda r: r.score,
                reverse=True,
            )
            for r in flagged_normals[:5]:
                print(f"    {r.wallet_address[:16]}... score={r.score:.3f}")
                for reason in r.reasons[:3]:
                    print(f"      - {reason}")

    # Overall accuracy
    total = len(all_results)
    correct = sum(1 for r in all_results if r.correct)
    print(f"\nOverall Accuracy: {correct}/{total} ({correct / total:.1%})")
    print(f"Flagging Threshold: {FLAGGING_THRESHOLD}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    # Filter to specific case if requested
    case_filter = None
    if "--case" in sys.argv:
        try:
            idx = sys.argv.index("--case")
            case_filter = sys.argv[idx + 1].lower()
        except (IndexError, ValueError):
            pass

    cases = BACKTEST_CASES
    if case_filter:
        cases = [c for c in cases if case_filter in c.name.lower()]
        if not cases:
            logger.error("No backtest case matching '%s'", case_filter)
            return

    all_results: list[BacktestResult] = []
    for case in cases:
        results = await run_backtest_case(case, verbose=verbose)
        all_results.extend(results)

    print_summary(all_results)


if __name__ == "__main__":
    asyncio.run(main())
