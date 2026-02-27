# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 6: Position Concentration

Measures how concentrated a wallet's capital is across their resolved positions.

Two measures:
1. Max concentration: fraction of total capital in the single largest bet
2. HHI (Herfindahl-Hirschman Index): sum of squared capital shares

Insiders often go all-in on a single bet they know will win, producing
very high concentration. Normal bettors tend to diversify.

Flag: >50% of capital in one bet that won, especially on niche markets.

Both concentration and HHI are computed on resolved positions only, since
the flag checks whether the largest bet won.

Output: output/position_concentration.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "position_concentration.parquet"

# Thresholds
FLAG_MAX_CONCENTRATION = 0.50  # >50% in single bet
MIN_BETS = 3  # Need at least 3 bets for concentration to be meaningful
NICHE_VOLUME_PERCENTILE = 0.25


def compute_position_concentration(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute position concentration from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered).
    Returns per-wallet concentration stats with raw values.
    """
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )

    if len(resolved) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    resolved_markets = resolved.select("market_id", "market_volume").unique(subset="market_id")
    niche_threshold = resolved_markets.select(
        pl.col("market_volume").quantile(NICHE_VOLUME_PERCENTILE)
    ).item()

    wallet_totals = resolved.group_by("wallet").agg(
        pl.col("total_usd_in").sum().alias("wallet_total_capital"),
        pl.col("market_id").count().alias("total_positions"),
    )

    positions_with_share = resolved.join(wallet_totals, on="wallet", how="left")

    positions_with_share = positions_with_share.with_columns(
        pl.when(pl.col("wallet_total_capital") > 0)
        .then(pl.col("total_usd_in") / pl.col("wallet_total_capital"))
        .otherwise(pl.lit(0.0))
        .alias("capital_share"),
    )

    wallet_stats = positions_with_share.group_by("wallet").agg(
        pl.col("total_positions").first(),
        pl.col("wallet_total_capital").first(),
        pl.col("capital_share").max().alias("max_concentration"),
        (pl.col("capital_share") ** 2).sum().alias("hhi"),
    )

    largest_positions = (
        positions_with_share
        .group_by("wallet")
        .agg(
            pl.col("market_id").sort_by("capital_share", descending=True).first().alias("largest_bet_market"),
            pl.col("total_usd_in").sort_by("capital_share", descending=True).first().alias("largest_bet_usd"),
            pl.col("capital_share").sort_by("capital_share", descending=True).first().alias("_largest_share"),
            pl.col("position_won").sort_by("capital_share", descending=True).first().alias("largest_bet_won"),
            pl.col("market_volume").sort_by("capital_share", descending=True).first().alias("largest_bet_market_volume"),
            pl.col("side").sort_by("capital_share", descending=True).first().alias("largest_bet_side"),
            pl.col("resolution").sort_by("capital_share", descending=True).first().alias("largest_bet_resolution"),
        )
    )

    wallet_stats = wallet_stats.join(
        largest_positions.drop("_largest_share"),
        on="wallet",
        how="left",
    )

    wallet_stats = wallet_stats.with_columns(
        pl.col("largest_bet_won").fill_null(False),
    )

    wallet_stats = wallet_stats.with_columns(
        (pl.col("largest_bet_market_volume") <= niche_threshold).fill_null(False).alias("largest_bet_is_niche"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("max_concentration") > FLAG_MAX_CONCENTRATION)
            & (pl.col("total_positions") >= MIN_BETS)
            & (pl.col("largest_bet_won") == True)
        ).alias("flagged"),
        (
            (pl.col("max_concentration") > FLAG_MAX_CONCENTRATION)
            & (pl.col("total_positions") >= MIN_BETS)
            & (pl.col("largest_bet_won") == True)
            & (pl.col("largest_bet_is_niche") == True)
        ).alias("flagged_niche"),
    )

    return wallet_stats.sort(
        ["flagged_niche", "flagged", "max_concentration"],
        descending=[True, True, True],
    )


def main():
    print("=" * 60)
    print("Metric 6: Position Concentration")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_positions.py first."
        )

    print("Loading wallet positions...")
    positions = pl.read_parquet(INPUT_PATH)
    print(f"  Total positions: {len(positions):,}")

    wallet_stats = compute_position_concentration(positions)

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = wallet_stats.filter(pl.col("flagged"))
    flagged_niche = wallet_stats.filter(pl.col("flagged_niche"))

    print(f"\nSummary:")
    print(f"  Total wallets: {len(wallet_stats):,}")
    print(f"  Mean max concentration: {wallet_stats.select(pl.col('max_concentration').mean()).item():.1%}")
    print(f"  Mean HHI: {wallet_stats.select(pl.col('hhi').mean()).item():.4f}")
    print(
        f"  Flagged wallets (>{FLAG_MAX_CONCENTRATION:.0%} concentration, "
        f"won, {MIN_BETS}+ bets): {len(flagged):,}"
    )
    print(f"  Flagged + niche market: {len(flagged_niche):,}")
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            niche_str = " [NICHE]" if row["largest_bet_is_niche"] else ""
            print(
                f"    {row['wallet'][:10]}... "
                f"max_conc={row['max_concentration']:.1%} "
                f"HHI={row['hhi']:.3f} "
                f"largest=${row['largest_bet_usd']:,.0f} "
                f"of ${row['wallet_total_capital']:,.0f} total"
                f"{niche_str}"
            )
    print("Done!")


if __name__ == "__main__":
    main()
