# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 3: Niche Market Accuracy

Measures win rate specifically on low-volume ("niche") markets.

Niche markets have less public attention and liquidity, so consistently
winning on them is harder for normal bettors but easier for insiders
who have private information about obscure events.

Definition: "niche" = markets with volume below the 25th percentile of
all resolved markets.

Flag: win rate > 70% with 5+ niche market bets.

The 70% threshold is intentionally higher than contrarian_win_rate's 60%
because niche market accuracy is less discriminating on its own — many
small-market bettors are domain experts. The higher bar reduces false
positives while still catching extreme cases.

Output: output/niche_market_accuracy.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(DATA_ROOT / "analyze" / "signal1")))
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "niche_market_accuracy.parquet"

# Thresholds
NICHE_PERCENTILE = 0.25  # Bottom 25th percentile by volume
MIN_NICHE_BETS = 5
FLAG_WIN_RATE = 0.70


def main():
    print("=" * 60)
    print("Metric 3: Niche Market Accuracy")
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

    # Filter to resolved markets only
    print("  Filtering to resolved markets...", flush=True)
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )
    print(f"  Resolved positions: {len(resolved):,}", flush=True)

    # Ensure position_won has no nulls (fill with False to prevent null propagation)
    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    # Determine niche volume threshold from unique resolved markets
    # Use unique(subset="market_id") to avoid float precision issues with market_volume
    print("  Computing niche thresholds...", flush=True)
    unique_markets = resolved.select("market_id", "market_volume").unique(subset="market_id")
    volume_threshold = unique_markets.select(
        pl.col("market_volume").quantile(NICHE_PERCENTILE)
    ).item()
    print(f"  Niche volume threshold (p{NICHE_PERCENTILE:.0%}): ${volume_threshold:,.0f}")

    n_niche_markets = unique_markets.filter(
        pl.col("market_volume") <= volume_threshold
    ).height
    print(f"  Niche markets: {n_niche_markets:,} / {unique_markets.height:,}")

    # Filter to niche markets
    niche_positions = resolved.filter(
        pl.col("market_volume") <= volume_threshold
    )
    print(f"  Niche market positions: {len(niche_positions):,}")

    # Per-wallet niche market stats
    print("  Aggregating per-wallet niche stats...", flush=True)
    wallet_stats = niche_positions.group_by("wallet").agg(
        pl.col("market_id").count().alias("niche_bet_count"),
        pl.col("position_won").sum().alias("niche_wins"),
        pl.col("total_usd_in").sum().alias("niche_usd_deployed"),
        pl.col("avg_entry_price").mean().alias("mean_niche_entry_price"),
        pl.col("market_volume").mean().alias("mean_market_volume"),
    )

    # Compute win rate
    wallet_stats = wallet_stats.with_columns(
        (pl.col("niche_wins") / pl.col("niche_bet_count")).alias("niche_win_rate"),
    )

    # Also compute their overall win rate for comparison
    print("  Computing overall win rates for comparison...", flush=True)
    overall_stats = resolved.group_by("wallet").agg(
        pl.col("market_id").count().alias("total_bet_count"),
        pl.col("position_won").sum().alias("total_wins"),
    ).with_columns(
        (pl.col("total_wins") / pl.col("total_bet_count")).alias("overall_win_rate"),
    )

    wallet_stats = wallet_stats.join(
        overall_stats.select("wallet", "total_bet_count", "overall_win_rate"),
        on="wallet",
        how="left",
    )

    # Compute niche vs overall win rate delta
    wallet_stats = wallet_stats.with_columns(
        (pl.col("niche_win_rate") - pl.col("overall_win_rate")).alias("niche_vs_overall_delta"),
    )

    # Add flag
    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("niche_win_rate") > FLAG_WIN_RATE)
            & (pl.col("niche_bet_count") >= MIN_NICHE_BETS)
        ).alias("flagged"),
    )

    # Sort: flagged first, then by niche win rate
    wallet_stats = wallet_stats.sort(
        ["flagged", "niche_win_rate", "niche_bet_count"],
        descending=[True, True, True],
    )

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = wallet_stats.filter(pl.col("flagged"))
    print(f"\nSummary:")
    print(f"  Wallets with niche bets: {len(wallet_stats):,}")
    print(f"  Flagged wallets (win rate > {FLAG_WIN_RATE:.0%}, {MIN_NICHE_BETS}+ bets): {len(flagged):,}")
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            print(
                f"    {row['wallet'][:10]}... "
                f"niche_wr={row['niche_win_rate']:.1%} "
                f"overall_wr={row['overall_win_rate']:.1%} "
                f"delta={row['niche_vs_overall_delta']:+.1%} "
                f"({row['niche_wins']}/{row['niche_bet_count']} bets)"
            )
    print("Done!")


if __name__ == "__main__":
    main()
