# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 4: Profit Factor

Per wallet across all resolved markets: sum of profits / sum of losses.

For each position (with sells tracked):
  - resolution_payout = net_tokens * 1.0 if position won, else 0
    (net_tokens = tokens_bought - tokens_sold, floored at 0; remaining tokens
    redeem at $1 each on the winning side)
  - net_profit = resolution_payout + total_usd_out - total_usd_in
    (total_usd_out = revenue from selling tokens before resolution)

  If net_profit > 0 -> it's a winning position (profit = net_profit)
  If net_profit < 0 -> it's a losing position (loss = abs(net_profit))

Profit factor = gross_profit / gross_loss

A profit factor > 5x with meaningful volume is extremely rare for
legitimate bettors and warrants investigation.

Flag: profit factor > 5.0 with $1,000+ total volume and 5+ resolved bets.

Output: output/profit_factor.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "profit_factor.parquet"

# Thresholds
FLAG_PROFIT_FACTOR = 5.0
MIN_VOLUME = 1_000.0
MIN_RESOLVED_BETS = 5


def compute_profit_factor(positions: pl.DataFrame) -> pl.DataFrame:
    """Compute profit factor from a positions DataFrame.

    Accepts wallet_positions-format DataFrame (can be pre-filtered).
    Returns per-wallet profit factor stats with raw values.
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
    ).with_columns(
        pl.when(pl.col("net_pnl") > 0)
        .then(pl.col("net_pnl"))
        .otherwise(pl.lit(0.0))
        .alias("profit"),
        pl.when(pl.col("net_pnl") < 0)
        .then(pl.col("net_pnl").abs())
        .otherwise(pl.lit(0.0))
        .alias("loss"),
    )

    wallet_stats = resolved_with_pnl.group_by("wallet").agg(
        pl.col("market_id").count().alias("resolved_bet_count"),
        pl.col("position_won").sum().alias("wins"),
        pl.col("total_usd_in").sum().alias("total_volume"),
        pl.col("profit").sum().alias("gross_profit"),
        pl.col("loss").sum().alias("gross_loss"),
        pl.col("net_pnl").sum().alias("net_pnl"),
    )

    wallet_stats = wallet_stats.with_columns(
        pl.when(pl.col("gross_loss") > 0)
        .then(pl.col("gross_profit") / pl.col("gross_loss"))
        .otherwise(
            pl.when(pl.col("gross_profit") > 0)
            .then(pl.lit(999.0))
            .otherwise(pl.lit(0.0))
        )
        .alias("profit_factor"),
        (pl.col("wins") / pl.col("resolved_bet_count")).alias("win_rate"),
    )

    wallet_stats = wallet_stats.with_columns(
        (
            (pl.col("profit_factor") > FLAG_PROFIT_FACTOR)
            & (pl.col("total_volume") >= MIN_VOLUME)
            & (pl.col("resolved_bet_count") >= MIN_RESOLVED_BETS)
        ).alias("flagged"),
    )

    return wallet_stats.sort(
        ["flagged", "profit_factor", "total_volume"],
        descending=[True, True, True],
    )


def main():
    print("=" * 60)
    print("Metric 4: Profit Factor")
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

    wallet_stats = compute_profit_factor(positions)

    # Write output
    print(f"\nWriting {len(wallet_stats):,} wallet records to {OUTPUT_PATH}")
    wallet_stats.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = wallet_stats.filter(pl.col("flagged"))
    print(f"\nSummary:")
    print(f"  Wallets with resolved bets: {len(wallet_stats):,}")
    print(
        f"  Flagged wallets (PF > {FLAG_PROFIT_FACTOR}x, "
        f"${MIN_VOLUME:,.0f}+ vol, {MIN_RESOLVED_BETS}+ bets): {len(flagged):,}"
    )
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            pf_str = f"{row['profit_factor']:.1f}x" if row['profit_factor'] < 999 else "INF"
            print(
                f"    {row['wallet'][:10]}... "
                f"PF={pf_str} "
                f"net_pnl=${row['net_pnl']:,.0f} "
                f"vol=${row['total_volume']:,.0f} "
                f"({row['wins']}/{row['resolved_bet_count']} wins)"
            )
    print("Done!")


if __name__ == "__main__":
    main()
