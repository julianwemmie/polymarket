# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 5: Brier Score

Computes each wallet's prediction accuracy using Brier scores.

The Brier score measures calibration: how close a bettor's implied
probability (entry price) is to the actual outcome (0 or 1).

  Brier score = mean( (entry_price - outcome)^2 )

Lower Brier score = better calibration.

DESIGN CHOICE: We use avg_entry_price as the wallet's implied probability
prediction. This is by design per the plan ("compare their implied probability
(entry price) against market consensus at time of entry"). The entry price
is the VWAP at which the wallet bought tokens, which directly maps to the
implied probability that the side will win.

We also compute the "naive" Brier score (if you just bet at 50/50 on
everything) and the market-consensus Brier score (using the capital-weighted
average entry price across all bettors in that market as the consensus).

A wallet whose Brier score is significantly better than the market
consensus is suspiciously well-calibrated.

Flag: wallet brier_skill_vs_consensus > 0.20 with 10+ resolved bets
(i.e., 20%+ improvement over market consensus, per the plan's "significantly
better than market consensus" criterion).

Output: output/brier_score.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "brier_score.parquet"

# Thresholds
FLAG_BRIER_SKILL = 0.20  # 20%+ improvement vs consensus = suspicious
MIN_RESOLVED_BETS = 10


def compute_brier_score(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute Brier score metrics from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered).
    Returns per-wallet Brier score stats with raw values.
    """
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )

    if len(resolved) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    resolved_with_brier = resolved.with_columns(
        pl.col("position_won").cast(pl.Float64).alias("outcome"),
    ).with_columns(
        ((pl.col("avg_entry_price") - pl.col("outcome")) ** 2).alias("brier_component"),
    )

    market_consensus = (
        resolved_with_brier
        .group_by(["market_id", "side"])
        .agg(
            (pl.col("avg_entry_price") * pl.col("total_usd_in")).sum().alias("_weighted_price"),
            pl.col("total_usd_in").sum().alias("_total_vol"),
            pl.col("outcome").first().alias("outcome"),
        )
        .with_columns(
            (pl.col("_weighted_price") / pl.col("_total_vol")).alias("consensus_price"),
        )
        .with_columns(
            ((pl.col("consensus_price") - pl.col("outcome")) ** 2).alias("consensus_brier"),
        )
        .select("market_id", "side", "consensus_price", "consensus_brier")
    )

    resolved_with_consensus = resolved_with_brier.join(
        market_consensus, on=["market_id", "side"], how="left"
    )

    wallet_stats = resolved_with_consensus.group_by("wallet").agg(
        pl.col("market_id").count().alias("resolved_bet_count"),
        (pl.col("brier_component") * pl.col("total_usd_in")).sum().alias("_weighted_brier"),
        (pl.col("consensus_brier") * pl.col("total_usd_in")).sum().alias("_weighted_consensus_brier"),
        pl.col("total_usd_in").sum().alias("total_volume"),
        pl.col("position_won").sum().alias("wins"),
        pl.col("avg_entry_price").mean().alias("mean_entry_price"),
    ).with_columns(
        (pl.col("_weighted_brier") / pl.col("total_volume")).alias("brier_score"),
        (pl.col("_weighted_consensus_brier") / pl.col("total_volume")).alias("market_consensus_brier"),
    ).drop(["_weighted_brier", "_weighted_consensus_brier"])

    NAIVE_BRIER = 0.25

    wallet_stats = wallet_stats.with_columns(
        (1.0 - pl.col("brier_score") / NAIVE_BRIER).alias("brier_skill_vs_naive"),
        pl.when(pl.col("market_consensus_brier") > 0)
        .then(1.0 - pl.col("brier_score") / pl.col("market_consensus_brier"))
        .otherwise(pl.lit(0.0))
        .alias("brier_skill_vs_consensus"),
        (pl.col("wins") / pl.col("resolved_bet_count")).alias("win_rate"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("brier_skill_vs_consensus") > FLAG_BRIER_SKILL)
            & (pl.col("resolved_bet_count") >= MIN_RESOLVED_BETS)
        ).alias("flagged"),
    )

    return wallet_stats.sort(
        ["flagged", "brier_skill_vs_consensus"],
        descending=[True, True],
    )


def main():
    print("=" * 60)
    print("Metric 5: Brier Score")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_positions.py first."
        )

    print("Loading wallet positions...")
    positions = pl.read_parquet(INPUT_PATH)

    wallet_stats = compute_brier_score(positions)

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = wallet_stats.filter(pl.col("flagged"))
    overall_brier = wallet_stats.select(pl.col("brier_score").mean()).item()
    overall_consensus = wallet_stats.select(pl.col("market_consensus_brier").mean()).item()

    print(f"\nSummary:")
    print(f"  Wallets with resolved bets: {len(wallet_stats):,}")
    print(f"  Overall mean Brier score: {overall_brier:.4f}")
    print(f"  Overall mean consensus Brier: {overall_consensus:.4f}")
    print(f"  Naive Brier (0.5 on everything): {NAIVE_BRIER:.4f}")
    print(
        f"  Flagged wallets (skill vs consensus > {FLAG_BRIER_SKILL:.0%}, "
        f"{MIN_RESOLVED_BETS}+ bets): {len(flagged):,}"
    )
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets (best skill vs consensus):")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            print(
                f"    {row['wallet'][:10]}... "
                f"brier={row['brier_score']:.4f} "
                f"skill_vs_market={row['brier_skill_vs_consensus']:+.2f} "
                f"win_rate={row['win_rate']:.1%} "
                f"({row['resolved_bet_count']} bets)"
            )
    print("Done!")


if __name__ == "__main__":
    main()
