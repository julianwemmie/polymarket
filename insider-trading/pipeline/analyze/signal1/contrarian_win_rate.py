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
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "contrarian_win_rate.parquet"

# Thresholds
CONTRARIAN_PRICE_THRESHOLD = 0.20  # Bought at < 20% implied odds
MIN_CONTRARIAN_BETS = 5
FLAG_WIN_RATE = 0.60


def compute_contrarian_win_rate(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute contrarian win rate from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered).
    Returns per-wallet contrarian win rate stats with raw values.
    """
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )

    if len(resolved) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    contrarian = resolved.filter(
        pl.col("avg_entry_price") < CONTRARIAN_PRICE_THRESHOLD
    )

    if len(contrarian) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    wallet_stats = contrarian.group_by("wallet").agg(
        pl.col("market_id").count().alias("contrarian_bet_count"),
        pl.col("position_won").sum().alias("contrarian_wins"),
        pl.col("total_usd_in").sum().alias("contrarian_usd_deployed"),
        pl.col("avg_entry_price").mean().alias("mean_contrarian_entry_price"),
    )

    wallet_stats = wallet_stats.with_columns(
        (pl.col("contrarian_wins") / pl.col("contrarian_bet_count"))
        .fill_null(0.0)
        .alias("contrarian_win_rate"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("contrarian_win_rate") > FLAG_WIN_RATE)
            & (pl.col("contrarian_bet_count") >= MIN_CONTRARIAN_BETS)
        ).alias("flagged"),
    )

    return wallet_stats.sort(
        ["flagged", "contrarian_win_rate", "contrarian_bet_count"],
        descending=[True, True, True],
    )


def main():
    print("=" * 60)
    print("Metric 2: Contrarian Win Rate")
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

    output = compute_contrarian_win_rate(positions)

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
