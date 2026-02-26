# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 9: Bet Size vs Odds

Analyzes whether a wallet places disproportionately large bets at extreme
odds (< 10% or > 90% implied probability).

Normal bettors reduce position size at extreme odds due to Kelly criterion
or risk aversion. Insiders who know the outcome increase size at extreme
odds because they know the longshot will pay off (or the near-certainty
will fail).

For each position, we compute:
  - potential_payout = total_usd_in * (1 / price - 1)
    = absolute USD profit if the position wins
  - Whether the bet was at extreme odds

Flag: wallets placing large absolute bets ($10K+) at extreme odds
      with high win rate on those extreme bets.

Output: output/bet_size_vs_odds.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(SCRIPT_DIR / "output")))
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "bet_size_vs_odds.parquet"

# Thresholds
EXTREME_LOW_ODDS = 0.10   # < 10% implied probability (longshot)
EXTREME_HIGH_ODDS = 0.90  # > 90% implied probability (near-certainty)
LARGE_BET_THRESHOLD = 10_000.0  # $10K+
MIN_EXTREME_BETS = 3
FLAG_EXTREME_WIN_RATE = 0.60


def main():
    print("=" * 60)
    print("Metric 9: Bet Size vs Odds")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_wallet_positions.py first."
        )

    # Load wallet positions
    print("Loading wallet positions...", flush=True)
    positions = pl.read_parquet(INPUT_PATH)
    print(f"  Loaded {len(positions):,} positions", flush=True)

    # Filter to resolved markets
    print("  Filtering to resolved markets...", flush=True)
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )
    print(f"  Resolved positions: {len(resolved):,}", flush=True)

    # Ensure position_won has no nulls
    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    # Add odds classification and payout calculations
    print("  Classifying odds and computing payouts...", flush=True)
    resolved_with_odds = resolved.with_columns(
        # Potential payout in absolute USD: net_tokens * (1 - avg_entry_price)
        # This is the profit on the remaining position if the tokens win
        # (each token redeems at $1, so profit per token = 1 - cost_basis).
        # Guard against null avg_entry_price (sell-only positions).
        pl.when(pl.col("avg_entry_price").is_not_null() & (pl.col("avg_entry_price") > 0))
        .then(pl.col("net_tokens") * (1.0 - pl.col("avg_entry_price")))
        .otherwise(pl.lit(0.0))
        .alias("potential_payout_usd"),
        # Payout ratio per $1 (kept for reference)
        pl.when(pl.col("avg_entry_price") > 0)
        .then((1.0 / pl.col("avg_entry_price")) - 1.0)
        .otherwise(pl.lit(0.0))
        .alias("payout_ratio"),
        # Classify odds range
        pl.when(pl.col("avg_entry_price") < EXTREME_LOW_ODDS)
        .then(pl.lit("extreme_longshot"))
        .when(pl.col("avg_entry_price") > EXTREME_HIGH_ODDS)
        .then(pl.lit("extreme_favorite"))
        .when(pl.col("avg_entry_price") < 0.30)
        .then(pl.lit("longshot"))
        .when(pl.col("avg_entry_price") > 0.70)
        .then(pl.lit("favorite"))
        .otherwise(pl.lit("mid_range"))
        .alias("odds_category"),
        # Is this an extreme odds bet?
        (
            (pl.col("avg_entry_price") < EXTREME_LOW_ODDS)
            | (pl.col("avg_entry_price") > EXTREME_HIGH_ODDS)
        ).alias("is_extreme_odds"),
        # Is this a large bet?
        (pl.col("total_usd_in") >= LARGE_BET_THRESHOLD).alias("is_large_bet"),
    )

    # ---- Per-wallet: extreme longshot behavior (< 10%) ----
    print("  Analyzing extreme longshots...", flush=True)
    extreme_longshots = resolved_with_odds.filter(
        pl.col("avg_entry_price") < EXTREME_LOW_ODDS
    )

    longshot_stats = extreme_longshots.group_by("wallet").agg(
        pl.col("market_id").count().alias("longshot_bet_count"),
        pl.col("position_won").sum().alias("longshot_wins"),
        pl.col("total_usd_in").sum().alias("longshot_usd_deployed"),
        pl.col("total_usd_in").max().alias("longshot_max_bet_usd"),
        pl.col("avg_entry_price").mean().alias("longshot_mean_entry"),
        pl.col("potential_payout_usd").mean().alias("longshot_mean_potential_payout"),
    ).with_columns(
        (pl.col("longshot_wins") / pl.col("longshot_bet_count")).alias("longshot_win_rate"),
    )

    # ---- Per-wallet: extreme high-odds behavior (> 90%) ----
    print("  Analyzing extreme favorites...", flush=True)
    extreme_favorites = resolved_with_odds.filter(
        pl.col("avg_entry_price") > EXTREME_HIGH_ODDS
    )

    favorite_stats = extreme_favorites.group_by("wallet").agg(
        pl.col("market_id").count().alias("favorite_bet_count"),
        pl.col("position_won").sum().alias("favorite_wins"),
        pl.col("total_usd_in").sum().alias("favorite_usd_deployed"),
        pl.col("total_usd_in").max().alias("favorite_max_bet_usd"),
        pl.col("avg_entry_price").mean().alias("favorite_mean_entry"),
        pl.col("potential_payout_usd").mean().alias("favorite_mean_potential_payout"),
    ).with_columns(
        # For favorites, we care about LOSS rate (insiders know the favorite will fail)
        (pl.col("favorite_wins") / pl.col("favorite_bet_count")).alias("favorite_win_rate"),
        (1.0 - pl.col("favorite_wins") / pl.col("favorite_bet_count")).alias("favorite_loss_rate"),
    )

    # ---- Large bets at extreme odds (the most suspicious pattern) ----
    print("  Analyzing large bets at extreme odds...", flush=True)
    large_extreme = resolved_with_odds.filter(
        pl.col("is_extreme_odds") & pl.col("is_large_bet")
    )

    large_extreme_stats = large_extreme.group_by("wallet").agg(
        pl.col("market_id").count().alias("large_extreme_count"),
        pl.col("position_won").sum().alias("large_extreme_wins"),
        pl.col("total_usd_in").sum().alias("large_extreme_usd"),
        pl.col("total_usd_in").max().alias("largest_extreme_bet_usd"),
        pl.col("avg_entry_price").mean().alias("large_extreme_mean_entry"),
    ).with_columns(
        (pl.col("large_extreme_wins") / pl.col("large_extreme_count"))
        .fill_null(0.0)
        .alias("large_extreme_win_rate"),
    )

    # ---- Overall wallet stats for context ----
    print("  Computing overall wallet stats...", flush=True)
    overall = resolved_with_odds.group_by("wallet").agg(
        pl.col("market_id").count().alias("total_bet_count"),
        pl.col("total_usd_in").sum().alias("total_volume"),
        pl.col("total_usd_in").mean().alias("avg_bet_size"),
        pl.col("position_won").sum().alias("total_wins"),
        # Average bet size at different odds ranges for Kelly analysis
        pl.col("total_usd_in")
        .filter(pl.col("odds_category") == "mid_range")
        .mean()
        .alias("avg_bet_size_mid_range"),
        pl.col("total_usd_in")
        .filter(pl.col("is_extreme_odds"))
        .mean()
        .alias("avg_bet_size_extreme"),
    ).with_columns(
        (pl.col("total_wins") / pl.col("total_bet_count")).alias("overall_win_rate"),
    )

    # Compute "inverse Kelly" ratio: if extreme bet size > mid-range bet size,
    # the wallet is doing the opposite of rational bankroll management
    overall = overall.with_columns(
        pl.when(
            pl.col("avg_bet_size_mid_range").is_not_null()
            & (pl.col("avg_bet_size_mid_range") > 0)
            & pl.col("avg_bet_size_extreme").is_not_null()
        )
        .then(pl.col("avg_bet_size_extreme") / pl.col("avg_bet_size_mid_range"))
        .otherwise(pl.lit(None))
        .alias("extreme_to_midrange_size_ratio"),
    )

    # Join all stats together
    print("  Joining all stats...", flush=True)
    wallet_stats = overall.join(longshot_stats, on="wallet", how="left")
    wallet_stats = wallet_stats.join(favorite_stats, on="wallet", how="left")
    wallet_stats = wallet_stats.join(large_extreme_stats, on="wallet", how="left")

    # Fill nulls for wallets with no extreme/longshot/favorite bets
    wallet_stats = wallet_stats.with_columns(
        pl.col("longshot_bet_count").fill_null(0),
        pl.col("longshot_wins").fill_null(0),
        pl.col("longshot_win_rate").fill_null(0.0),
        pl.col("favorite_bet_count").fill_null(0),
        pl.col("favorite_wins").fill_null(0),
        pl.col("favorite_win_rate").fill_null(0.0),
        pl.col("favorite_loss_rate").fill_null(0.0),
        pl.col("large_extreme_count").fill_null(0),
        pl.col("large_extreme_wins").fill_null(0),
        pl.col("large_extreme_win_rate").fill_null(0.0),
    )

    # Add flags
    #
    # NOTE: flagged_favorite_loser was removed because it catches wallets that
    # bet on >90% favorites and lose frequently -- i.e., bad bettors, not insiders.
    # An insider who knows a favorite will fail would SHORT the favorite (buy the
    # underdog at <10%), which is already captured by the longshot analysis.
    # The extreme_favorites stats are kept for informational purposes.
    wallet_stats = wallet_stats.with_columns(
        # Primary flag: large bets at extreme odds with high win rate
        (
            (pl.col("large_extreme_count") >= MIN_EXTREME_BETS)
            & (pl.col("large_extreme_win_rate") > FLAG_EXTREME_WIN_RATE)
        ).alias("flagged_large_extreme"),
        # Secondary flag: high longshot win rate
        (
            (pl.col("longshot_bet_count") >= MIN_EXTREME_BETS)
            & (pl.col("longshot_win_rate") > FLAG_EXTREME_WIN_RATE)
        ).alias("flagged_longshot_winner"),
        # Tertiary flag: inverse Kelly behavior (bets bigger at extreme odds)
        (
            pl.col("extreme_to_midrange_size_ratio").is_not_null()
            & (pl.col("extreme_to_midrange_size_ratio") > 2.0)
        ).alias("flagged_inverse_kelly"),
    )

    # Combined flag: any of the above
    wallet_stats = wallet_stats.with_columns(
        (
            pl.col("flagged_large_extreme")
            | pl.col("flagged_longshot_winner")
            | pl.col("flagged_inverse_kelly")
        ).alias("flagged"),
    )

    # Sort with nulls_last to handle nullable columns gracefully
    wallet_stats = wallet_stats.sort(
        ["flagged_large_extreme", "flagged", "large_extreme_usd"],
        descending=[True, True, True],
        nulls_last=True,
    )

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged_le = wallet_stats.filter(pl.col("flagged_large_extreme"))
    flagged_lw = wallet_stats.filter(pl.col("flagged_longshot_winner"))
    flagged_ik = wallet_stats.filter(pl.col("flagged_inverse_kelly"))
    flagged_any = wallet_stats.filter(pl.col("flagged"))

    print(f"\nSummary:")
    print(f"  Total wallets: {len(wallet_stats):,}")
    print(f"  Flagged (large extreme bets + high WR): {len(flagged_le):,}")
    print(f"  Flagged (longshot winner): {len(flagged_lw):,}")
    print(f"  Flagged (inverse Kelly): {len(flagged_ik):,}")
    print(f"  Flagged (any): {len(flagged_any):,}")

    if len(flagged_le) > 0:
        print(f"\n  Top 10 large-extreme-bet flagged wallets:")
        top10 = flagged_le.head(10)
        for row in top10.iter_rows(named=True):
            print(
                f"    {row['wallet'][:10]}... "
                f"large_extreme={row['large_extreme_count']} bets "
                f"WR={row['large_extreme_win_rate']:.1%} "
                f"largest=${row['largest_extreme_bet_usd']:,.0f} "
                f"total_vol=${row['total_volume']:,.0f}"
            )
    print("Done!")


if __name__ == "__main__":
    main()
