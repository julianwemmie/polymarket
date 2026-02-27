# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Signal 2 - Step 3: Pre-Spike Wallets

For each detected price spike, looks backward into the trade stream to find
ALL wallets that traded during the pre-spike window (between
PRE_SPIKE_START_HOURS and PRE_SPIKE_END_MINUTES before the spike).

Both correct-direction and incorrect-direction trades are captured so that
downstream timing_score.py can compute hit_rate (fraction of trades in the
correct direction).

Input:
  - output/price_spikes.parquet            (from step 2)
  - data/ingest/trades/ (partitioned Parquet)
Output:
  - output/pre_spike_trades.parquet

Columns produced:
  - wallet (str)                  wallet address (maker or taker)
  - market_id (i64)
  - spike_id (u64)
  - entry_timestamp (datetime)    when the wallet traded
  - lead_time_minutes (f64)       minutes before the spike started
  - usd_amount (f64)              size of the trade in USD
  - entry_price (f64)             price at which the trade occurred
  - direction (str)               "up" or "down" (spike direction)
  - side (str)                    "BUY" or "SELL" (wallet's action)
  - correct_direction (bool)      True if BUY before up-spike or SELL before down-spike

Strategy:
  1. Load price_spikes.parquet and compute pre-spike time windows.
  2. Iterate through trade part-files. For each part:
     a. Filter to only market_ids that have spikes.
     b. Join trades with spike windows on market_id (vectorized).
     c. Filter to trades where timestamp falls within the pre-spike window.
     d. Compute correct_direction and lead_time_minutes.
  3. Concat all matches and write to parquet.

Memory: Spikes table stays in memory. Trades processed one part-file at a time.
  The join temporarily expands each chunk (~trades × spikes_per_market)
  but the timestamp filter immediately reduces it.

Usage:
  cd pipeline/analyze/signal2
  uv run python pre_spike_wallets.py
"""

import os
import time
from pathlib import Path
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
PRE_SPIKE_START_HOURS = 4         # How far back before spike_start to look (hours)
PRE_SPIKE_END_MINUTES = 30        # Stop looking this close to spike_start (minutes)
MIN_USD_AMOUNT = 1.0              # Minimum trade size to consider (filter dust)

# ---------------------------------------------------------------------------
# Paths (override via POLYMARKET_DATA_DIR for Modal)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal2"
SPIKES_FILE = OUTPUT_DIR / "price_spikes.parquet"
TRADES_DIR = DATA_ROOT / "ingest" / "trades"
OUTPUT_FILE = OUTPUT_DIR / "pre_spike_trades.parquet"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_SCHEMA = {
    "wallet": pl.String,
    "market_id": pl.Int64,
    "spike_id": pl.UInt64,
    "entry_timestamp": pl.Datetime,
    "lead_time_minutes": pl.Float64,
    "usd_amount": pl.Float64,
    "entry_price": pl.Float64,
    "direction": pl.String,
    "side": pl.String,
    "correct_direction": pl.Boolean,
}


def build_spike_windows(spikes: pl.DataFrame) -> pl.DataFrame:
    """
    Augment the spikes DataFrame with pre-spike window boundaries.

    Adds:
      - window_start: spike_start_ts - PRE_SPIKE_START_HOURS
      - window_end:   spike_start_ts - PRE_SPIKE_END_MINUTES
    """
    return spikes.with_columns(
        (pl.col("spike_start_ts") - timedelta(hours=PRE_SPIKE_START_HOURS)).alias("window_start"),
        (pl.col("spike_start_ts") - timedelta(minutes=PRE_SPIKE_END_MINUTES)).alias("window_end"),
    )


def extract_all_wallet_trades(chunk: pl.DataFrame) -> pl.DataFrame:
    """
    Extract wallet-level trade records from a chunk, capturing BOTH buy and
    sell sides so that hit_rate can be computed downstream.

    Deduplicates neg-risk double-counts: in neg-risk markets, a single
    economic action creates two OrderFilled events in the same transaction.
    The wallet appears in both (once as maker, once as taker) with the same
    market/side/direction, inflating volumes. We keep only one entry per
    (transactionHash, wallet, market_id, side).
    """
    maker_records = chunk.select(
        pl.col("maker").alias("wallet"),
        "timestamp",
        "market_id",
        pl.col("maker_direction").alias("side"),
        "price",
        "usd_amount",
        "transactionHash",
    )

    taker_records = chunk.select(
        pl.col("taker").alias("wallet"),
        "timestamp",
        "market_id",
        pl.col("taker_direction").alias("side"),
        "price",
        "usd_amount",
        "transactionHash",
    )

    combined = pl.concat([maker_records, taker_records])

    combined = combined.unique(
        subset=["transactionHash", "wallet", "market_id", "side"],
        keep="first",
    )

    return combined.drop("transactionHash")


def find_pre_spike_wallets(spikes_df: pl.DataFrame, trades_df: pl.DataFrame) -> pl.DataFrame:
    """Find wallets that traded in pre-spike windows.

    Accepts spikes (from detect_spikes_for_market) and trades DataFrames.
    Returns pre-spike trades in the same schema as pre_spike_trades.parquet.
    """
    if len(spikes_df) == 0 or len(trades_df) == 0:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)

    spikes = build_spike_windows(spikes_df)

    spike_windows = spikes.select(
        "spike_id", "market_id", "direction", "window_start", "window_end", "spike_start_ts",
    )
    spike_market_ids = spikes["market_id"].unique().to_list()

    # Filter trades to spike markets and minimum size
    chunk = trades_df.filter(
        pl.col("market_id").is_in(spike_market_ids)
        & (pl.col("usd_amount") >= MIN_USD_AMOUNT)
    )

    if len(chunk) == 0:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)

    wallet_trades = extract_all_wallet_trades(chunk)

    if len(wallet_trades) == 0:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)

    wallet_trades = wallet_trades.with_columns(
        pl.col("side").str.to_uppercase(),
    )

    joined = wallet_trades.join(spike_windows, on="market_id", how="inner")

    matched = joined.filter(
        (pl.col("timestamp") >= pl.col("window_start"))
        & (pl.col("timestamp") <= pl.col("window_end"))
    )

    if len(matched) == 0:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)

    matched = matched.with_columns(
        ((pl.col("spike_start_ts") - pl.col("timestamp")).dt.total_minutes()).alias("lead_time_minutes"),
        (
            ((pl.col("side") == "BUY") & (pl.col("direction") == "up"))
            | ((pl.col("side") == "SELL") & (pl.col("direction") == "down"))
        ).alias("correct_direction"),
    ).select(
        "wallet", "market_id", "spike_id",
        pl.col("timestamp").alias("entry_timestamp"),
        "lead_time_minutes", "usd_amount",
        pl.col("price").alias("entry_price"),
        "direction", "side", "correct_direction",
    )

    # Deduplicate
    result = (
        matched
        .group_by(["wallet", "spike_id", "entry_timestamp", "market_id",
                    "direction", "side", "correct_direction"])
        .agg(
            pl.col("usd_amount").sum(),
            pl.col("entry_price").mean(),
            pl.col("lead_time_minutes").first(),
        )
    )

    return result.sort("spike_id", "wallet", "entry_timestamp")


def main() -> None:
    print(f"[pre_spike_wallets] Starting...")
    print(f"  Spikes file:      {SPIKES_FILE}")
    print(f"  Trades dir:       {TRADES_DIR}")
    print(f"  Output:           {OUTPUT_FILE}")
    print(f"  Pre-spike window: {PRE_SPIKE_END_MINUTES}min to {PRE_SPIKE_START_HOURS}hrs before spike")
    print(f"  Min USD amount:   ${MIN_USD_AMOUNT}")
    print(flush=True)

    if not SPIKES_FILE.exists():
        raise FileNotFoundError(
            f"Spikes file not found: {SPIKES_FILE}\n"
            "Run detect_price_spikes.py first."
        )
    if not TRADES_DIR.exists():
        raise FileNotFoundError(f"Trades directory not found: {TRADES_DIR}")

    part_files = sorted(TRADES_DIR.glob("*.parquet"))
    if not part_files:
        raise FileNotFoundError(f"No Parquet files found in {TRADES_DIR}")

    t0 = time.time()

    # Load spikes and build windows
    print("  Loading spikes...", flush=True)
    spikes = pl.read_parquet(SPIKES_FILE)
    print(f"  Loaded {len(spikes):,} spikes across {spikes['market_id'].n_unique():,} markets", flush=True)

    if len(spikes) == 0:
        print("No spikes to analyze. Exiting.")
        pl.DataFrame(schema=OUTPUT_SCHEMA).write_parquet(OUTPUT_FILE)
        return

    spikes = build_spike_windows(spikes)

    print(f"  Up spikes: {spikes.filter(pl.col('direction') == 'up').height:,}, "
          f"Down spikes: {spikes.filter(pl.col('direction') == 'down').height:,}", flush=True)

    # Prepare spike windows DataFrame for vectorized join (replaces Python dict loop)
    spike_windows = spikes.select(
        "spike_id", "market_id", "direction", "window_start", "window_end", "spike_start_ts",
    )
    spike_market_ids = spikes["market_id"].unique().to_list()

    # Iterate through trade part-files and match against spike windows via join
    print(f"\n  Reading {len(part_files)} trade part-files...", flush=True)

    all_matches: list[pl.DataFrame] = []
    total_rows = 0
    total_matches = 0
    chunk_count = 0

    for part_file in part_files:
        chunk = pl.read_parquet(
            part_file,
            columns=["timestamp", "market_id", "maker", "taker",
                      "maker_direction", "taker_direction", "price", "usd_amount",
                      "transactionHash"],
        )
        chunk_count += 1
        total_rows += len(chunk)

        # Filter to only markets with spikes and trades above minimum size
        chunk = chunk.filter(
            pl.col("market_id").is_in(spike_market_ids)
            & (pl.col("usd_amount") >= MIN_USD_AMOUNT)
        )

        if len(chunk) == 0:
            elapsed = time.time() - t0
            print(f"  [{chunk_count}/{len(part_files)}] no spike-market trades - {elapsed:.1f}s", flush=True)
            continue

        # Extract ALL wallet trades (both BUY and SELL) from maker+taker
        wallet_trades = extract_all_wallet_trades(chunk)

        if len(wallet_trades) == 0:
            continue

        # Normalize side to uppercase to handle "buy"/"sell" or "BUY"/"SELL"
        wallet_trades = wallet_trades.with_columns(
            pl.col("side").str.to_uppercase(),
        )

        # Pre-filter spikes to only those whose window overlaps this chunk's
        # time range. Without this, the join explodes (1M trades × 1.3M spikes).
        chunk_min_ts = wallet_trades["timestamp"].min()
        chunk_max_ts = wallet_trades["timestamp"].max()
        relevant_spikes = spike_windows.filter(
            (pl.col("window_end") >= chunk_min_ts)
            & (pl.col("window_start") <= chunk_max_ts)
        )

        if len(relevant_spikes) == 0:
            elapsed = time.time() - t0
            print(f"  [{chunk_count}/{len(part_files)}] no overlapping spikes - {elapsed:.1f}s", flush=True)
            continue

        # Vectorized join: pair each trade with all temporally-relevant spikes
        # in the same market, then filter to the exact pre-spike window.
        joined = wallet_trades.join(relevant_spikes, on="market_id", how="inner")

        matched = joined.filter(
            (pl.col("timestamp") >= pl.col("window_start"))
            & (pl.col("timestamp") <= pl.col("window_end"))
        )

        if len(matched) == 0:
            elapsed = time.time() - t0
            print(f"  [{chunk_count}/{len(part_files)}] 0 temporal matches - {elapsed:.1f}s", flush=True)
            continue

        # Compute derived fields
        matched = matched.with_columns(
            ((pl.col("spike_start_ts") - pl.col("timestamp")).dt.total_minutes()).alias("lead_time_minutes"),
            (
                ((pl.col("side") == "BUY") & (pl.col("direction") == "up"))
                | ((pl.col("side") == "SELL") & (pl.col("direction") == "down"))
            ).alias("correct_direction"),
        ).select(
            "wallet",
            "market_id",
            "spike_id",
            pl.col("timestamp").alias("entry_timestamp"),
            "lead_time_minutes",
            "usd_amount",
            pl.col("price").alias("entry_price"),
            "direction",
            "side",
            "correct_direction",
        )

        all_matches.append(matched)
        total_matches += len(matched)

        elapsed = time.time() - t0
        print(f"  [{chunk_count}/{len(part_files)}] "
              f"{total_matches:,} total matches - {elapsed:.1f}s", flush=True)

    # Combine all matches
    if not all_matches:
        print("\nNo pre-spike trades found.")
        result = pl.DataFrame(schema=OUTPUT_SCHEMA)
    else:
        print(f"\n  Combining {len(all_matches)} match batches...", flush=True)
        result = pl.concat(all_matches)
        # Deduplicate: same wallet + spike_id + entry_timestamp
        result = (
            result
            .group_by(["wallet", "spike_id", "entry_timestamp", "market_id",
                        "direction", "side", "correct_direction"])
            .agg(
                pl.col("usd_amount").sum(),
                pl.col("entry_price").mean(),
                pl.col("lead_time_minutes").first(),
            )
        )
        result = result.sort("spike_id", "wallet", "entry_timestamp")

    print(f"  Writing output...", flush=True)
    result.write_parquet(OUTPUT_FILE)

    elapsed = time.time() - t0
    print(f"\n[pre_spike_wallets] Done.")
    print(f"  Total rows scanned:     {total_rows:,}")
    print(f"  Pre-spike trades found: {len(result):,}")
    if len(result) > 0:
        correct_count = result.filter(pl.col("correct_direction")).height
        print(f"  Correct-direction:      {correct_count:,} ({correct_count / len(result) * 100:.1f}%)")
        print(f"  Unique wallets:         {result['wallet'].n_unique():,}")
        print(f"  Unique spikes matched:  {result['spike_id'].n_unique():,}")
        print(f"  Avg lead time:          {result['lead_time_minutes'].mean():.1f} min")
        print(f"  Total USD in window:    ${result['usd_amount'].sum():,.0f}")
    print(f"  Output file:            {OUTPUT_FILE}")
    print(f"  Output size:            {OUTPUT_FILE.stat().st_size / 1e6:.1f} MB")
    print(f"  Elapsed:                {elapsed:.1f}s")


if __name__ == "__main__":
    main()
