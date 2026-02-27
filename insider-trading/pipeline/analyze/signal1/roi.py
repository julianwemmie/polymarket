# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 8: Return on Investment (ROI)

Per wallet: net_profit / total_capital_deployed across resolved markets.

Context matters: 500% ROI on 3 bets is far more suspicious than 500% ROI
on 500 bets. We capture both the raw ROI and the bet count to allow
downstream analysis to weight accordingly.

Also computes annualized ROI based on the wallet's active trading period,
capped at 10000% (100.0) to prevent overflow from short-duration wallets.

ROI per position = (net_tokens * 1.0 * position_won + total_usd_out - total_usd_in) / total_usd_in

NOTE: trading_span is computed from resolved bets only (first_trade_timestamp
to last_trade_timestamp in wallet_positions). This may understate the actual
trading period if the wallet has unresolved trades. A more accurate span
would require reading from the raw trades, which is not done here for
performance reasons.

Flag: ROI > 200% with $500+ deployed and 5+ resolved bets.

Output: output/roi.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "roi.parquet"

# Thresholds
FLAG_ROI = 2.0  # 200%
MIN_VOLUME = 500.0
MIN_RESOLVED_BETS = 5
MAX_ANNUALIZED_ROI = 100.0  # Cap at 10000% to prevent overflow


def compute_roi(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute ROI metrics from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered by market or wallet).
    Returns per-wallet ROI stats with raw values (not percentile-ranked).
    """
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )

    if len(resolved) == 0:
        return pl.DataFrame({"wallet": []}, schema={"wallet": pl.String})

    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    resolved_with_pnl = resolved.with_columns(
        pl.when(pl.col("position_won"))
        .then(pl.col("net_tokens") * 1.0)
        .otherwise(pl.lit(0.0))
        .alias("resolution_payout"),
    ).with_columns(
        (pl.col("resolution_payout") + pl.col("total_usd_out") - pl.col("total_usd_in"))
        .alias("net_pnl"),
        pl.when(pl.col("position_won"))
        .then(pl.col("net_tokens") + pl.col("total_usd_out"))
        .otherwise(pl.col("total_usd_out"))
        .alias("payout"),
    )

    wallet_stats = resolved_with_pnl.group_by("wallet").agg(
        pl.col("market_id").count().alias("resolved_bet_count"),
        pl.col("position_won").sum().alias("wins"),
        pl.col("total_usd_in").sum().alias("total_capital_deployed"),
        pl.col("payout").sum().alias("total_payout"),
        pl.col("net_pnl").sum().alias("net_profit"),
        pl.col("first_trade_timestamp").min().alias("first_trade"),
        pl.col("last_trade_timestamp").max().alias("last_trade"),
    )

    wallet_stats = wallet_stats.with_columns(
        pl.when(pl.col("total_capital_deployed") > 0)
        .then(pl.col("net_profit") / pl.col("total_capital_deployed"))
        .otherwise(pl.lit(0.0))
        .alias("roi"),
        (pl.col("wins") / pl.col("resolved_bet_count")).alias("win_rate"),
    )

    first_trade_col = wallet_stats.schema["first_trade"]
    if first_trade_col == pl.Utf8 or first_trade_col == pl.String:
        wallet_stats = wallet_stats.with_columns(
            pl.col("first_trade").str.to_datetime().alias("_first_dt"),
            pl.col("last_trade").str.to_datetime().alias("_last_dt"),
        )
    else:
        wallet_stats = wallet_stats.with_columns(
            pl.col("first_trade").alias("_first_dt"),
            pl.col("last_trade").alias("_last_dt"),
        )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("_last_dt").cast(pl.Int64) - pl.col("_first_dt").cast(pl.Int64))
            .truediv(1_000_000)
            .truediv(86400)
        ).alias("trading_span_days"),
    ).drop(["_first_dt", "_last_dt"])

    wallet_stats = wallet_stats.with_columns(
        pl.when(
            (pl.col("trading_span_days") > 30) & (pl.col("roi") >= -1.0)
        )
        .then(
            ((1.0 + pl.col("roi")).pow(365.0 / pl.col("trading_span_days"))) - 1.0
        )
        .when(
            (pl.col("trading_span_days") > 30) & (pl.col("roi") < -1.0)
        )
        .then(pl.lit(-1.0))
        .otherwise(pl.lit(None))
        .alias("annualized_roi"),
    )

    wallet_stats = wallet_stats.with_columns(
        pl.col("annualized_roi").clip(-1.0, MAX_ANNUALIZED_ROI).alias("annualized_roi"),
    )

    wallet_stats = wallet_stats.with_columns(
        (pl.col("net_profit") / pl.col("resolved_bet_count")).alias("profit_per_bet"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("roi") > FLAG_ROI)
            & (pl.col("total_capital_deployed") >= MIN_VOLUME)
            & (pl.col("resolved_bet_count") >= MIN_RESOLVED_BETS)
        ).alias("flagged"),
    )

    return wallet_stats.sort(
        ["flagged", "roi", "total_capital_deployed"],
        descending=[True, True, True],
    )


def main():
    print("=" * 60)
    print("Metric 8: ROI (Return on Investment)")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_positions.py first."
        )

    print("Loading wallet positions...")
    positions = pl.read_parquet(INPUT_PATH)

    wallet_stats = compute_roi(positions)

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = wallet_stats.filter(pl.col("flagged"))
    median_roi = wallet_stats.select(pl.col("roi").median()).item()

    print(f"\nSummary:")
    print(f"  Wallets with resolved bets: {len(wallet_stats):,}")
    print(f"  Median ROI: {median_roi:.1%}")
    print(
        f"  Flagged wallets (ROI > {FLAG_ROI:.0%}, "
        f"${MIN_VOLUME:,.0f}+ vol, {MIN_RESOLVED_BETS}+ bets): {len(flagged):,}"
    )
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            span_str = f"{row['trading_span_days']:.0f}d" if row['trading_span_days'] is not None else "N/A"
            print(
                f"    {row['wallet'][:10]}... "
                f"ROI={row['roi']:.0%} "
                f"net=${row['net_profit']:,.0f} "
                f"deployed=${row['total_capital_deployed']:,.0f} "
                f"({row['wins']}/{row['resolved_bet_count']} wins) "
                f"span={span_str}"
            )
    print("Done!")


if __name__ == "__main__":
    main()
