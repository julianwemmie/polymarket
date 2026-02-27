# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Metric 7: Win Streak

Orders each wallet's resolved bets chronologically and finds the
longest consecutive winning streak.

A long win streak is statistically unlikely for random or even skilled
bettors. The famous ricosuave666 case was 7/7 on resolved markets.

Flag: longest streak > 7 on resolved markets (i.e., streak >= 8).
Per the plan: "streak > 7".

Output: output/win_streak.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal1"
INPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"
OUTPUT_PATH = OUTPUT_DIR / "win_streak.parquet"

# Thresholds
# Plan says "streak > 7", so we flag streaks of 8 or more
FLAG_STREAK_LENGTH = 8
MIN_RESOLVED_BETS = 5


def compute_longest_streak(wins: list[bool]) -> int:
    """Compute longest consecutive True streak in a list of booleans."""
    max_streak = 0
    current_streak = 0
    for won in wins:
        if won:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def compute_current_streak(wins: list[bool]) -> int:
    """Compute current (trailing) streak length."""
    streak = 0
    for won in reversed(wins):
        if won:
            streak += 1
        else:
            break
    return streak


def main():
    print("=" * 60)
    print("Metric 7: Win Streak")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"wallet_positions.parquet not found at {INPUT_PATH}. "
            "Run build_positions.py first."
        )

    # Load wallet positions
    print("Loading wallet positions...")
    positions = pl.read_parquet(INPUT_PATH)

    # Filter to resolved markets only
    resolved = positions.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    )
    print(f"  Resolved positions: {len(resolved):,}")

    if len(resolved) == 0:
        print("No resolved positions found. Writing empty output.")
        pl.DataFrame({"wallet": []}).write_parquet(str(OUTPUT_PATH))
        print("Done!")
        return

    # Fill position_won nulls before streak computation
    resolved = resolved.with_columns(
        pl.col("position_won").fill_null(False),
    )

    # Ensure first_trade_timestamp is datetime for correct chronological ordering.
    # If stored as string (e.g., epoch-style or ISO strings), lexicographic sort
    # gives wrong order. Cast to Datetime if not already.
    ts_dtype = resolved.schema["first_trade_timestamp"]
    if ts_dtype == pl.Utf8 or ts_dtype == pl.String:
        resolved = resolved.with_columns(
            pl.col("first_trade_timestamp").str.to_datetime(strict=False),
        )
    elif ts_dtype not in (pl.Datetime, pl.Date):
        # Attempt a general cast for other numeric types (e.g., Int64 epoch)
        resolved = resolved.with_columns(
            pl.col("first_trade_timestamp").cast(pl.Datetime, strict=False),
        )

    # Sort by wallet and first_trade_timestamp to get chronological order
    resolved_sorted = resolved.sort(["wallet", "first_trade_timestamp"])

    # Group by wallet and collect ordered list of win/loss outcomes.
    # Use sort_by inside agg to guarantee chronological order within each group,
    # since group_by does NOT preserve sort order in Polars.
    wallet_sequences = (
        resolved_sorted
        .group_by("wallet")
        .agg(
            pl.col("position_won").sort_by("first_trade_timestamp").alias("win_sequence"),
            pl.col("market_id").sort_by("first_trade_timestamp").alias("market_sequence"),
            pl.col("first_trade_timestamp").sort_by("first_trade_timestamp").alias("timestamp_sequence"),
            pl.col("total_usd_in").sum().alias("total_volume"),
            pl.col("position_won").sum().alias("total_wins"),
            pl.col("position_won").count().alias("resolved_bet_count"),
        )
    )

    # Compute streaks using map_elements on the list column
    wallet_stats = wallet_sequences.with_columns(
        pl.col("win_sequence")
        .map_elements(compute_longest_streak, return_dtype=pl.Int64)
        .alias("longest_win_streak"),
        pl.col("win_sequence")
        .map_elements(compute_current_streak, return_dtype=pl.Int64)
        .alias("current_streak"),
        (pl.col("total_wins") / pl.col("resolved_bet_count")).alias("win_rate"),
    )

    # Compute expected longest streak using actual win rate.
    # For n independent trials with win probability p, the expected longest run
    # of successes is approximately log(n) / log(1/p).
    # We report actual / expected ratio.
    wallet_stats = wallet_stats.with_columns(
        pl.when((pl.col("win_rate") > 0) & (pl.col("win_rate") < 1.0))
        .then(
            pl.col("longest_win_streak")
            / (
                pl.col("resolved_bet_count").cast(pl.Float64).log()
                / (1.0 / pl.col("win_rate")).log()
            )
        )
        .otherwise(
            # If win_rate is 0 or 1, the formula degenerates; use simple ratio
            pl.when(pl.col("win_rate") == 1.0)
            .then(pl.lit(1.0))  # Perfect record: streak == n, expected == n
            .otherwise(pl.lit(0.0))  # No wins: streak is 0
        )
        .alias("streak_vs_expected_ratio"),
    )

    # Drop list columns for output
    output = wallet_stats.drop(["win_sequence", "market_sequence", "timestamp_sequence"])

    # Add flag: streak > 7 (i.e., >= 8)
    output = output.with_columns(
        (
            (pl.col("longest_win_streak") >= FLAG_STREAK_LENGTH)
            & (pl.col("resolved_bet_count") >= MIN_RESOLVED_BETS)
        ).alias("flagged"),
    )

    # Sort: flagged first, then by streak length
    output = output.sort(
        ["flagged", "longest_win_streak", "resolved_bet_count"],
        descending=[True, True, True],
    )

    # Write output
    print(f"\nWriting {len(output):,} wallet records to {OUTPUT_PATH}")
    output.write_parquet(OUTPUT_PATH)

    # Summary
    flagged = output.filter(pl.col("flagged"))
    avg_streak = output.select(pl.col("longest_win_streak").mean()).item()

    print(f"\nSummary:")
    print(f"  Wallets with resolved bets: {len(output):,}")
    print(f"  Average longest win streak: {avg_streak:.1f}")
    print(
        f"  Flagged wallets (streak >= {FLAG_STREAK_LENGTH}, "
        f"{MIN_RESOLVED_BETS}+ bets): {len(flagged):,}"
    )
    if len(flagged) > 0:
        print(f"\n  Top 10 flagged wallets:")
        top10 = flagged.head(10)
        for row in top10.iter_rows(named=True):
            print(
                f"    {row['wallet'][:10]}... "
                f"streak={row['longest_win_streak']} "
                f"win_rate={row['win_rate']:.1%} "
                f"({row['total_wins']}/{row['resolved_bet_count']} bets) "
                f"vol=${row['total_volume']:,.0f}"
            )
    print("Done!")


if __name__ == "__main__":
    main()
