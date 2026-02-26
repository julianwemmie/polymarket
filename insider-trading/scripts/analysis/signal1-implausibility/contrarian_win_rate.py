# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 2: Contrarian Win Rate

Finds bets where a wallet bought at < 20% implied odds (avg_entry_price < 0.20)
and checks if the market resolved in their favor.

Wallets that consistently win contrarian bets are statistically implausible
unless they have private information.

Flag: win rate > 60% with 5+ contrarian bets.

Output: output/contrarian_win_rate.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(SCRIPT_DIR / "output")))
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "contrarian_win_rate.parquet"

# Thresholds
CONTRARIAN_PRICE_THRESHOLD = 0.20  # Bought at < 20% implied odds
MIN_CONTRARIAN_BETS = 5
FLAG_WIN_RATE = 0.60


def main():
    print("=" * 60)
    print("Metric 2: Contrarian Win Rate")
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

    # Filter to resolved markets only (token1 or token2 won)
    print("  Filtering to resolved markets...", flush=True)
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )
    print(f"  Resolved positions: {len(resolved):,}", flush=True)

    # Ensure position_won has no nulls (fill with False to prevent null propagation in sum/count)
    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    # Filter to contrarian bets: entry price < threshold
    # Use avg_entry_price which is VWAP of buys only (cost basis)
    contrarian = resolved.filter(
        pl.col("avg_entry_price") < CONTRARIAN_PRICE_THRESHOLD
    )
    print(f"  Contrarian positions (entry < {CONTRARIAN_PRICE_THRESHOLD}): {len(contrarian):,}", flush=True)

    # Per-wallet contrarian stats
    print("  Aggregating per-wallet stats...", flush=True)
    wallet_stats = contrarian.group_by("wallet").agg(
        pl.col("market_id").count().alias("contrarian_bet_count"),
        pl.col("position_won").sum().alias("contrarian_wins"),
        pl.col("total_usd_in").sum().alias("contrarian_usd_deployed"),
        pl.col("avg_entry_price").mean().alias("mean_contrarian_entry_price"),
        # Track the specific markets for context
        pl.col("market_id").alias("contrarian_market_ids"),
    )

    # Compute win rate (contrarian_wins is guaranteed non-null after fill_null above)
    wallet_stats = wallet_stats.with_columns(
        (pl.col("contrarian_wins") / pl.col("contrarian_bet_count"))
        .fill_null(0.0)
        .alias("contrarian_win_rate"),
    )

    # Add flag
    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("contrarian_win_rate") > FLAG_WIN_RATE)
            & (pl.col("contrarian_bet_count") >= MIN_CONTRARIAN_BETS)
        ).alias("flagged"),
    )

    # Sort by win rate descending (flagged first)
    wallet_stats = wallet_stats.sort(
        ["flagged", "contrarian_win_rate", "contrarian_bet_count"],
        descending=[True, True, True],
    )

    # Drop the list column for cleaner parquet (it can be huge)
    output = wallet_stats.drop("contrarian_market_ids")

    # Write output
    print(f"\nWriting {len(output):,} wallet records to {OUTPUT_PATH}")
    output.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = output.filter(pl.col("flagged"))
    print(f"\nSummary:")
    print(f"  Wallets with contrarian bets: {len(output):,}")
    print(f"  Flagged wallets (win rate > {FLAG_WIN_RATE:.0%}, {MIN_CONTRARIAN_BETS}+ bets): {len(flagged):,}")
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            print(
                f"    {row['wallet'][:10]}... "
                f"win_rate={row['contrarian_win_rate']:.1%} "
                f"({row['contrarian_wins']}/{row['contrarian_bet_count']} bets) "
                f"${row['contrarian_usd_deployed']:,.0f} deployed"
            )
    print("Done!")


if __name__ == "__main__":
    main()
