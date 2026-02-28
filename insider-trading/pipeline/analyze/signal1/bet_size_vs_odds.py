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
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "bet_size_vs_odds.parquet"

# Thresholds
EXTREME_LOW_ODDS = 0.10   # < 10% implied probability (longshot)
EXTREME_HIGH_ODDS = 0.90  # > 90% implied probability (near-certainty)
LARGE_BET_THRESHOLD = 10_000.0  # $10K+
MIN_EXTREME_BETS = 3
FLAG_EXTREME_WIN_RATE = 0.60


def compute_bet_size_vs_odds(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute bet size vs odds metrics from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered).
    Returns per-wallet bet size vs odds stats with raw values.
    """
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )

    if len(resolved) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    resolved_with_odds = resolved.with_columns(
        pl.when(pl.col("avg_entry_price").is_not_null() & (pl.col("avg_entry_price") > 0))
        .then(pl.col("net_tokens") * (1.0 - pl.col("avg_entry_price")))
        .otherwise(pl.lit(0.0))
        .alias("potential_payout_usd"),
        pl.when(pl.col("avg_entry_price") > 0)
        .then((1.0 / pl.col("avg_entry_price")) - 1.0)
        .otherwise(pl.lit(0.0))
        .alias("payout_ratio"),
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
        (
            (pl.col("avg_entry_price") < EXTREME_LOW_ODDS)
            | (pl.col("avg_entry_price") > EXTREME_HIGH_ODDS)
        ).alias("is_extreme_odds"),
        (pl.col("total_usd_in") >= LARGE_BET_THRESHOLD).alias("is_large_bet"),
    )

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
        (pl.col("favorite_wins") / pl.col("favorite_bet_count")).alias("favorite_win_rate"),
        (1.0 - pl.col("favorite_wins") / pl.col("favorite_bet_count")).alias("favorite_loss_rate"),
    )

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

    overall = resolved_with_odds.group_by("wallet").agg(
        pl.col("market_id").count().alias("total_bet_count"),
        pl.col("total_usd_in").sum().alias("total_volume"),
        pl.col("total_usd_in").mean().alias("avg_bet_size"),
        pl.col("position_won").sum().alias("total_wins"),
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

    wallet_stats = overall.join(longshot_stats, on="wallet", how="left")
    wallet_stats = wallet_stats.join(favorite_stats, on="wallet", how="left")
    wallet_stats = wallet_stats.join(large_extreme_stats, on="wallet", how="left")

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

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("large_extreme_count") >= MIN_EXTREME_BETS)
            & (pl.col("large_extreme_win_rate") > FLAG_EXTREME_WIN_RATE)
        ).alias("flagged_large_extreme"),
        (
            (pl.col("longshot_bet_count") >= MIN_EXTREME_BETS)
            & (pl.col("longshot_win_rate") > FLAG_EXTREME_WIN_RATE)
        ).alias("flagged_longshot_winner"),
        (
            pl.col("extreme_to_midrange_size_ratio").is_not_null()
            & (pl.col("extreme_to_midrange_size_ratio") > 2.0)
        ).alias("flagged_inverse_kelly"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            pl.col("flagged_large_extreme")
            | pl.col("flagged_longshot_winner")
            | pl.col("flagged_inverse_kelly")
        ).alias("flagged"),
    )

    return wallet_stats.sort(
        ["flagged_large_extreme", "flagged", "large_extreme_usd"],
        descending=[True, True, True],
        nulls_last=True,
    )


def main():
    print("=" * 60)
    print("Metric 9: Bet Size vs Odds")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_positions.py first."
        )

    print("Loading wallet positions...", flush=True)
    positions = pl.read_parquet(INPUT_PATH)
    print(f"  Loaded {len(positions):,} positions", flush=True)

    wallet_stats = compute_bet_size_vs_odds(positions)

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
