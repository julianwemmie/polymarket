# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Signal 2 - Step 4: Timing Score

Aggregates pre-spike trade appearances per wallet across all markets.
Flags repeat offenders who appear in pre-spike windows of multiple markets.

Computes a baseline expectation: given a wallet's overall trading activity,
how many pre-spike windows would they appear in by chance? Wallets that
significantly exceed this baseline are flagged.

Input:
  - output/pre_spike_trades.parquet   (from step 3)
  - output/price_spikes.parquet       (from step 2 -- for true spike counts)
  - ../../../historical-data/processed/trades.csv  (for baseline: total trades per wallet)
Output:
  - output/timing_scores.parquet

Columns produced:
  - wallet (str)
  - num_spikes_preceded (u32)     total spike events this wallet traded before
  - num_markets (u32)             distinct markets across those spikes
  - avg_lead_time_minutes (f64)   average minutes before spike they entered
  - median_lead_time_minutes (f64) median minutes before spike
  - total_pre_spike_usd (f64)    total USD deployed in pre-spike windows
  - hit_rate (f64)               fraction of pre-spike trades in the correct direction
  - total_trades_all (u64)       total trades by this wallet across all markets (baseline)
  - total_markets_all (u32)      total distinct markets this wallet traded in
  - spike_rate (f64)             num_spikes_preceded / total_markets_all (normalized)
  - expected_spikes (f64)        baseline expected spikes given activity level
  - excess_ratio (f64)           num_spikes_preceded / expected_spikes (>1 = suspicious)
  - is_flagged (bool)            True if num_markets >= MIN_MARKETS and excess_ratio > EXCESS_THRESHOLD

Note: MIN_SPIKE_APPEARANCES is kept as a secondary filter in addition to the
plan's primary "3+ different markets" threshold.

Usage:
  cd scripts/analysis/signal2-timing
  uv run python timing_score.py
"""

import os
import time
from pathlib import Path

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
MIN_SPIKE_APPEARANCES = 3         # Minimum distinct spike events to flag a wallet (secondary)
MIN_MARKETS = 3                   # Minimum distinct markets with pre-spike trades to flag (primary, per plan)
EXCESS_THRESHOLD = 2.0            # Excess ratio above which a wallet is flagged
                                  # (2.0 = 2x more spikes preceded than baseline expectation)
MIN_HIT_RATE = 0.50               # Minimum spike-level hit rate to flag a wallet
CHUNK_SIZE = 2_000_000            # Rows per chunk for trades.csv (baseline computation)

# Pre-spike window parameters (must match pre_spike_wallets.py)
PRE_SPIKE_START_HOURS = 4
PRE_SPIKE_END_MINUTES = 30

# ---------------------------------------------------------------------------
# Paths (override via POLYMARKET_DATA_DIR / POLYMARKET_OUTPUT_DIR for Modal)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(SCRIPT_DIR / ".." / ".." / ".." / "historical-data")))
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(SCRIPT_DIR / "output")))
PRE_SPIKE_FILE = OUTPUT_DIR / "pre_spike_trades.parquet"
SPIKES_FILE = OUTPUT_DIR / "price_spikes.parquet"
PRICE_HISTORY_FILE = OUTPUT_DIR / "price_history.parquet"
TRADES_CSV = DATA_DIR / "processed" / "trades.csv"
OUTPUT_FILE = OUTPUT_DIR / "timing_scores.parquet"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def compute_market_stats_from_price_history() -> tuple[float, int]:
    """
    Compute median market duration (in hours) and total market count from
    price_history.parquet.

    Returns:
        (median_market_duration_hours, total_markets_in_dataset)
    """
    print("  Computing market duration from price_history.parquet...")

    if not PRICE_HISTORY_FILE.exists():
        print("  WARNING: price_history.parquet not found, falling back to defaults")
        return 720.0, 0

    # Only load the columns we need to minimize memory
    ph = pl.scan_parquet(PRICE_HISTORY_FILE).select("market_id", "bucket_start")

    # Per-market: max(bucket_start) - min(bucket_start)
    market_durations = ph.group_by("market_id").agg(
        (pl.col("bucket_start").max() - pl.col("bucket_start").min()).alias("duration"),
    ).collect()

    total_markets = len(market_durations)

    if total_markets == 0:
        print("  WARNING: no markets found in price_history.parquet, falling back to defaults")
        return 720.0, 0

    # Convert duration to hours and take median
    market_durations = market_durations.with_columns(
        pl.col("duration").dt.total_hours().alias("duration_hours"),
    )

    # Filter out markets with zero duration (single bucket)
    nonzero = market_durations.filter(pl.col("duration_hours") > 0)
    if len(nonzero) == 0:
        median_hours = 720.0
    else:
        median_hours = nonzero["duration_hours"].median()

    print(f"  Total markets in dataset: {total_markets:,}")
    print(f"  Median market duration:   {median_hours:.1f} hours ({median_hours / 24:.1f} days)")

    return median_hours, total_markets


def compute_spike_level_hit_rate(pre_spike: pl.DataFrame) -> pl.DataFrame:
    """
    Compute spike-level hit rate per wallet.

    For each (wallet, spike_id), determines whether the wallet's NET position
    was in the correct direction (capital-weighted: sum of usd_amount for
    correct trades vs incorrect trades). Then computes hit_rate as the
    fraction of spikes where the net direction was correct.

    Returns DataFrame with columns: wallet (str), hit_rate (f64)
    """
    # Per (wallet, spike_id): compute capital-weighted correct direction.
    # correct_usd = sum of usd_amount where correct_direction is True
    # incorrect_usd = sum of usd_amount where correct_direction is False
    # Net correct if correct_usd > incorrect_usd
    spike_level = pre_spike.group_by("wallet", "spike_id").agg(
        pl.col("usd_amount").filter(pl.col("correct_direction")).sum().alias("correct_usd"),
        pl.col("usd_amount").filter(~pl.col("correct_direction")).sum().alias("incorrect_usd"),
    ).with_columns(
        # Fill nulls: when ALL trades are correct, incorrect_usd is null (no rows
        # matched the filter), and vice versa. Without fill_null, the comparison
        # correct_usd > null evaluates to null (not True), excluding the most
        # suspicious wallets from the hit_rate mean.
        pl.col("correct_usd").fill_null(0.0),
        pl.col("incorrect_usd").fill_null(0.0),
    ).with_columns(
        (pl.col("correct_usd") > pl.col("incorrect_usd")).alias("spike_correct"),
    )

    # Per wallet: fraction of spikes with correct net direction
    wallet_hit_rate = spike_level.group_by("wallet").agg(
        pl.col("spike_correct").mean().alias("hit_rate"),
    )

    return wallet_hit_rate


def compute_wallet_activity_baseline(
    wallets_of_interest: set[str],
) -> pl.DataFrame:
    """
    Stream through trades.csv and compute total trading activity per wallet
    (only for wallets that appear in pre-spike trades).

    Uses vectorized Polars operations: unpivots maker+taker into a single
    wallet column, filters, then aggregates with group_by.

    Returns DataFrame with columns:
      wallet (str), total_trades_all (u64), total_markets_all (u32)
    """
    print("  Computing wallet activity baseline from trades.csv...")

    wallets_list = list(wallets_of_interest)

    reader = pl.read_csv_batched(
        TRADES_CSV,
        batch_size=CHUNK_SIZE,
        schema_overrides={
            "market_id": pl.Int64,
            "maker": pl.String,
            "taker": pl.String,
        },
        columns=["market_id", "maker", "taker"],
    )

    # Accumulate per-chunk aggregates and combine at the end.
    # Each partial has (wallet, market_id, trade_count).
    accumulated: list[pl.DataFrame] = []
    chunk_count = 0
    total_rows = 0
    t0 = time.time()

    while True:
        batches = reader.next_batches(1)
        if batches is None or len(batches) == 0:
            break

        chunk = batches[0]
        chunk_count += 1
        total_rows += len(chunk)

        # Unpivot maker and taker into a single wallet column.
        # Each original row produces up to two wallet rows.
        maker_side = chunk.select(
            pl.col("maker").alias("wallet"),
            "market_id",
        ).filter(pl.col("wallet").is_in(wallets_list))

        taker_side = chunk.select(
            pl.col("taker").alias("wallet"),
            "market_id",
        ).filter(pl.col("wallet").is_in(wallets_list))

        wallet_chunk = pl.concat([maker_side, taker_side])

        if len(wallet_chunk) == 0:
            continue

        # Partial aggregate: count trades per wallet per market
        partial = wallet_chunk.group_by("wallet", "market_id").agg(
            pl.len().alias("trade_count"),
        )
        accumulated.append(partial)

        if chunk_count % 10 == 0:
            elapsed = time.time() - t0
            pct_done = total_rows / 151_000_000 * 100
            print(f"    Baseline chunk {chunk_count}/~76 ({pct_done:.0f}%) - {elapsed:.1f}s", flush=True)

        # Periodically compact
        if chunk_count % 50 == 0 and len(accumulated) > 20:
            combined = pl.concat(accumulated)
            combined = combined.group_by("wallet", "market_id").agg(
                pl.col("trade_count").sum(),
            )
            accumulated = [combined]

    if not accumulated:
        return pl.DataFrame(schema={
            "wallet": pl.String,
            "total_trades_all": pl.UInt64,
            "total_markets_all": pl.UInt32,
        })

    # Final aggregation
    combined = pl.concat(accumulated)
    combined = combined.group_by("wallet", "market_id").agg(
        pl.col("trade_count").sum(),
    )

    # Now aggregate across markets per wallet
    result = combined.group_by("wallet").agg(
        pl.col("trade_count").sum().cast(pl.UInt64).alias("total_trades_all"),
        pl.col("market_id").n_unique().cast(pl.UInt32).alias("total_markets_all"),
    )

    return result


def main() -> None:
    print(f"[timing_score] Starting...")
    print(f"  Pre-spike file:        {PRE_SPIKE_FILE}")
    print(f"  Spikes file:           {SPIKES_FILE}")
    print(f"  Trades CSV:            {TRADES_CSV}")
    print(f"  Output:                {OUTPUT_FILE}")
    print(f"  Min spike appearances: {MIN_SPIKE_APPEARANCES} (secondary)")
    print(f"  Min markets:           {MIN_MARKETS} (primary, per plan)")
    print(f"  Excess threshold:      {EXCESS_THRESHOLD}x")
    print(f"  Min hit rate:          {MIN_HIT_RATE}")
    print()

    if not PRE_SPIKE_FILE.exists():
        raise FileNotFoundError(
            f"Pre-spike trades not found: {PRE_SPIKE_FILE}\n"
            "Run pre_spike_wallets.py first."
        )
    if not SPIKES_FILE.exists():
        raise FileNotFoundError(
            f"Price spikes not found: {SPIKES_FILE}\n"
            "Run detect_price_spikes.py first."
        )
    if not TRADES_CSV.exists():
        raise FileNotFoundError(f"Trades CSV not found: {TRADES_CSV}")

    t0 = time.time()

    # Load pre-spike trades
    pre_spike = pl.read_parquet(PRE_SPIKE_FILE)
    print(f"  Loaded {len(pre_spike):,} pre-spike trades "
          f"({pre_spike['wallet'].n_unique():,} wallets, "
          f"{pre_spike['spike_id'].n_unique():,} spikes)")

    output_schema = {
        "wallet": pl.String,
        "num_spikes_preceded": pl.UInt32,
        "num_markets": pl.UInt32,
        "avg_lead_time_minutes": pl.Float64,
        "median_lead_time_minutes": pl.Float64,
        "total_pre_spike_usd": pl.Float64,
        "hit_rate": pl.Float64,
        "total_trades_all": pl.UInt64,
        "total_markets_all": pl.UInt32,
        "spike_rate": pl.Float64,
        "expected_spikes": pl.Float64,
        "excess_ratio": pl.Float64,
        "is_flagged": pl.Boolean,
    }

    if len(pre_spike) == 0:
        print("No pre-spike trades to score. Exiting.")
        pl.DataFrame(schema=output_schema).write_parquet(OUTPUT_FILE)
        return

    # Load price_spikes.parquet for true total spike/market counts (not from pre-spike table)
    spikes = pl.read_parquet(SPIKES_FILE)
    total_spikes = len(spikes)
    total_spike_markets = spikes["market_id"].n_unique()
    print(f"  Loaded {total_spikes:,} total spikes across {total_spike_markets:,} spike-having markets (from price_spikes.parquet)")

    # Compute market stats from price history (median duration + total market count)
    median_market_duration_hours, total_markets_in_dataset = compute_market_stats_from_price_history()

    # Fallback: if price history is unavailable, use spike markets as lower bound
    if total_markets_in_dataset == 0:
        print("  WARNING: Could not determine total markets, falling back to spike-having markets")
        total_markets_in_dataset = total_spike_markets

    # Compute spike-level hit rate per wallet
    print("  Computing spike-level hit rate per wallet...")
    wallet_hit_rate = compute_spike_level_hit_rate(pre_spike)

    # Aggregate per wallet -- hit_rate is computed separately at spike level
    print("  Aggregating per wallet...")
    wallet_agg = pre_spike.group_by("wallet").agg(
        pl.col("spike_id").n_unique().cast(pl.UInt32).alias("num_spikes_preceded"),
        pl.col("market_id").n_unique().cast(pl.UInt32).alias("num_markets"),
        pl.col("lead_time_minutes").mean().alias("avg_lead_time_minutes"),
        pl.col("lead_time_minutes").median().alias("median_lead_time_minutes"),
        pl.col("usd_amount").sum().alias("total_pre_spike_usd"),
    )

    # Join spike-level hit rate
    wallet_agg = wallet_agg.join(wallet_hit_rate, on="wallet", how="left").with_columns(
        pl.col("hit_rate").fill_null(0.0),
    )
    print(f"  Aggregated to {len(wallet_agg):,} wallets")

    # Compute baseline activity for wallets of interest
    wallets_of_interest = set(wallet_agg["wallet"].to_list())
    baseline = compute_wallet_activity_baseline(wallets_of_interest)

    # Join baseline with wallet aggregates
    wallet_scores = wallet_agg.join(baseline, on="wallet", how="left")

    # Fill nulls for wallets with no baseline data (shouldn't happen, but safety)
    wallet_scores = wallet_scores.with_columns(
        pl.col("total_trades_all").fill_null(pl.lit(0).cast(pl.UInt64)),
        pl.col("total_markets_all").fill_null(pl.lit(0).cast(pl.UInt32)),
    )

    # Compute spike_rate: what fraction of their markets had pre-spike trades
    # Guard against division by zero with clip
    wallet_scores = wallet_scores.with_columns(
        pl.when(pl.col("total_markets_all") > 0)
        .then(pl.col("num_spikes_preceded").cast(pl.Float64) / pl.col("total_markets_all").cast(pl.Float64))
        .otherwise(0.0)
        .alias("spike_rate"),
    )

    # Compute expected_spikes: baseline expectation incorporating window_fraction.
    #
    # For a wallet active in M markets, the expected number of spikes preceded by
    # chance is:
    #   E = M * (S / N) * window_fraction
    # where:
    #   S = total_spikes (from price_spikes.parquet)
    #   N = total_markets_in_dataset (ALL markets, not just spike-having ones)
    #   window_fraction = pre_spike_window_hours / median_market_duration_hours
    #     accounts for the probability that a random trade falls in the window
    pre_spike_window_hours = PRE_SPIKE_START_HOURS - (PRE_SPIKE_END_MINUTES / 60.0)
    window_fraction = min(pre_spike_window_hours / median_market_duration_hours, 1.0)

    global_spike_density = total_spikes / max(total_markets_in_dataset, 1)

    print(f"  Baseline: {total_spikes} spikes across {total_markets_in_dataset} total markets "
          f"(density={global_spike_density:.4f}), window_fraction={window_fraction:.6f}")
    print(f"  Median market duration: {median_market_duration_hours:.1f} hours")

    wallet_scores = wallet_scores.with_columns(
        (
            pl.col("total_markets_all").cast(pl.Float64)
            * global_spike_density
            * window_fraction
        ).alias("expected_spikes"),
    )

    # Compute excess_ratio -- clamp to avoid inf when expected_spikes is 0
    # If expected_spikes is 0 but wallet has preceded spikes, cap at a high value.
    MAX_EXCESS_RATIO = 100.0
    wallet_scores = wallet_scores.with_columns(
        pl.when(pl.col("expected_spikes") > 0)
        .then(
            (pl.col("num_spikes_preceded").cast(pl.Float64) / pl.col("expected_spikes"))
            .clip(0.0, MAX_EXCESS_RATIO)
        )
        .otherwise(
            pl.when(pl.col("num_spikes_preceded") > 0)
            .then(pl.lit(MAX_EXCESS_RATIO))
            .otherwise(0.0)
        )
        .alias("excess_ratio"),
    )

    # Flag wallets: primary threshold is MIN_MARKETS (per plan: "3+ different markets")
    # Secondary filters: MIN_SPIKE_APPEARANCES, excess_ratio, and hit_rate
    wallet_scores = wallet_scores.with_columns(
        (
            (pl.col("num_markets") >= MIN_MARKETS)
            & (pl.col("num_spikes_preceded") >= MIN_SPIKE_APPEARANCES)
            & (pl.col("excess_ratio") > EXCESS_THRESHOLD)
            & (pl.col("hit_rate") > MIN_HIT_RATE)
        ).alias("is_flagged"),
    )

    # Sort: flagged wallets first, then by excess_ratio descending
    wallet_scores = wallet_scores.sort(
        [pl.col("is_flagged"), pl.col("excess_ratio")],
        descending=[True, True],
    )

    wallet_scores.write_parquet(OUTPUT_FILE)

    elapsed = time.time() - t0
    flagged = wallet_scores.filter(pl.col("is_flagged"))

    print(f"\n[timing_score] Done.")
    print(f"  Total wallets scored:   {len(wallet_scores):,}")
    print(f"  Flagged wallets:        {len(flagged):,}")
    if len(flagged) > 0:
        print(f"  Flagged avg spikes:     {flagged['num_spikes_preceded'].mean():.1f}")
        print(f"  Flagged avg markets:    {flagged['num_markets'].mean():.1f}")
        print(f"  Flagged avg excess:     {flagged['excess_ratio'].mean():.2f}x")
        print(f"  Flagged avg lead time:  {flagged['avg_lead_time_minutes'].mean():.1f} min")
        print(f"  Flagged avg hit_rate:   {flagged['hit_rate'].mean():.2f}")
        print(f"  Flagged total USD:      ${flagged['total_pre_spike_usd'].sum():,.0f}")
    print(f"  Baseline spike density: {global_spike_density:.2f} spikes/market")
    print(f"  Window fraction:        {window_fraction:.6f}")
    print(f"  Output file:            {OUTPUT_FILE}")
    print(f"  Output size:            {OUTPUT_FILE.stat().st_size / 1e6:.1f} MB")
    print(f"  Elapsed:                {elapsed:.1f}s")


if __name__ == "__main__":
    main()
