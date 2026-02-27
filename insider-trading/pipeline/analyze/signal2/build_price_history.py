# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Signal 2 - Step 1: Build Price History

Reconstructs per-market price history from the raw trade stream.
For each market, computes time-weighted average price (TWAP) in fixed time
buckets -- a simple mean of trade prices within each bucket.

Input:  data/ingest/trades/ (partitioned Parquet)
Output: output/price_history.parquet

Columns produced:
  - market_id (i64)
  - bucket_start (datetime)
  - bucket_end (datetime)
  - avg_price (f64)       simple mean of trade prices in this bucket (TWAP)
  - num_trades (u32)      number of trades in this bucket
  - total_volume (f64)    total USD volume in this bucket

Memory: Processes trades in part-file batches. Accumulates bucket aggregates
        incrementally, then flushes to parquet at the end.

Usage:
  cd pipeline/analyze/signal2
  uv run python build_price_history.py
"""

import os
import time
from pathlib import Path
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
PRICE_BUCKET_MINUTES = 5          # Granularity of price history buckets
FLUSH_EVERY_N_CHUNKS = 50         # Flush accumulated data to reduce memory

# ---------------------------------------------------------------------------
# Paths (override via POLYMARKET_DATA_DIR for Modal)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
TRADES_DIR = DATA_ROOT / "ingest" / "trades"
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal2"
OUTPUT_FILE = OUTPUT_DIR / "price_history.parquet"

# ---------------------------------------------------------------------------
# Ensure output directory exists
# ---------------------------------------------------------------------------
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def bucket_start_expr(bucket_minutes: int) -> pl.Expr:
    """Truncate a timestamp column to the nearest bucket boundary."""
    return pl.col("timestamp").dt.truncate(f"{bucket_minutes}m")


def process_chunk(df: pl.DataFrame, bucket_minutes: int) -> pl.DataFrame:
    """
    Given a chunk of trades, compute per-(market_id, bucket) aggregates.

    Returns a DataFrame with columns:
      market_id, bucket_start, price_sum (sum), total_volume (sum), num_trades (count)

    We keep price_sum separate so we can combine across chunks before
    computing the final TWAP = sum(price_sum) / sum(num_trades).
    """
    df = df.with_columns(
        bucket_start_expr(bucket_minutes).alias("bucket_start"),
    )

    agg = df.group_by("market_id", "bucket_start").agg(
        pl.col("price").sum().alias("price_sum"),
        pl.col("usd_amount").sum().alias("total_volume"),
        pl.len().cast(pl.UInt32).alias("num_trades"),
    )
    return agg


def build_price_history_for_trades(trades_df: pl.DataFrame) -> pl.DataFrame:
    """Build price history from a pre-filtered trades DataFrame.

    Accepts trades in the standard ingest schema (can be pre-filtered by market).
    Returns price history in the same schema as price_history.parquet.
    """
    bucket_minutes = PRICE_BUCKET_MINUTES

    # Filter invalid rows
    clean = trades_df.filter(
        pl.col("price").is_not_null()
        & pl.col("usd_amount").is_not_null()
        & (pl.col("usd_amount") > 0)
        & pl.col("timestamp").is_not_null()
    )

    if len(clean) == 0:
        return pl.DataFrame(schema={
            "market_id": pl.Int64, "bucket_start": pl.Datetime("us"),
            "bucket_end": pl.Datetime("us"), "avg_price": pl.Float64,
            "num_trades": pl.UInt32, "total_volume": pl.Float64,
        })

    # Select only needed columns for processing
    clean = clean.select("timestamp", "market_id", "price", "usd_amount")

    agg = process_chunk(clean, bucket_minutes)

    # Compute TWAP and bucket_end
    bucket_delta = timedelta(minutes=bucket_minutes)
    result = agg.with_columns(
        (pl.col("price_sum") / pl.col("num_trades").cast(pl.Float64)).alias("avg_price"),
        (pl.col("bucket_start") + bucket_delta).alias("bucket_end"),
    ).select(
        "market_id", "bucket_start", "bucket_end",
        "avg_price", "num_trades", "total_volume",
    ).sort("market_id", "bucket_start")

    return result


def main() -> None:
    print(f"[build_price_history] Starting...")
    print(f"  Trades dir : {TRADES_DIR}")
    print(f"  Output     : {OUTPUT_FILE}")
    print(f"  Bucket size: {PRICE_BUCKET_MINUTES} minutes")
    print()

    if not TRADES_DIR.exists():
        raise FileNotFoundError(f"Trades directory not found: {TRADES_DIR}")

    part_files = sorted(TRADES_DIR.glob("*.parquet"))
    if not part_files:
        raise FileNotFoundError(f"No Parquet files found in {TRADES_DIR}")

    print(f"  Part files : {len(part_files)}")

    t0 = time.time()

    # We'll read part-files and accumulate partial aggregates.
    # Each partial aggregate has (market_id, bucket_start) -> (price_sum, total_volume, num_trades)
    # We periodically combine partials to keep memory bounded.

    accumulated: list[pl.DataFrame] = []
    total_rows = 0
    chunk_count = 0

    for part_file in part_files:
        chunk = pl.read_parquet(
            part_file,
            columns=["timestamp", "market_id", "price", "usd_amount"],
        )
        chunk_count += 1
        total_rows += len(chunk)

        # Drop rows where price or usd_amount is null or zero (no contribution to avg)
        chunk = chunk.filter(
            pl.col("price").is_not_null()
            & pl.col("usd_amount").is_not_null()
            & (pl.col("usd_amount") > 0)
            & pl.col("timestamp").is_not_null()
        )

        partial = process_chunk(chunk, PRICE_BUCKET_MINUTES)
        accumulated.append(partial)

        elapsed = time.time() - t0
        print(f"  [{chunk_count}/{len(part_files)}] {total_rows:,} rows | {elapsed:.1f}s")

        # Periodically combine accumulated partials to limit memory usage
        if chunk_count % FLUSH_EVERY_N_CHUNKS == 0:
            print(f"  Compacting {len(accumulated)} partial aggregates...")
            combined = pl.concat(accumulated)
            combined = combined.group_by("market_id", "bucket_start").agg(
                pl.col("price_sum").sum(),
                pl.col("total_volume").sum(),
                pl.col("num_trades").sum(),
            )
            accumulated = [combined]
            print(f"  Compacted to {len(combined):,} rows")

    # Final combination of all accumulated partials
    if not accumulated:
        print("No data processed. Exiting.")
        return

    print(f"\n  Final compaction of {len(accumulated)} partial aggregates...")
    combined = pl.concat(accumulated)
    combined = combined.group_by("market_id", "bucket_start").agg(
        pl.col("price_sum").sum(),
        pl.col("total_volume").sum(),
        pl.col("num_trades").sum(),
    )

    # Compute TWAP (simple mean of prices = sum of prices / count) and bucket_end
    bucket_delta = timedelta(minutes=PRICE_BUCKET_MINUTES)
    result = combined.with_columns(
        (pl.col("price_sum") / pl.col("num_trades").cast(pl.Float64)).alias("avg_price"),
        (pl.col("bucket_start") + bucket_delta).alias("bucket_end"),
    ).select(
        "market_id",
        "bucket_start",
        "bucket_end",
        "avg_price",
        "num_trades",
        "total_volume",
    ).sort("market_id", "bucket_start")

    # Write to parquet
    result.write_parquet(OUTPUT_FILE)

    elapsed = time.time() - t0
    print(f"\n[build_price_history] Done.")
    print(f"  Total rows processed: {total_rows:,}")
    print(f"  Output rows:          {len(result):,}")
    print(f"  Output file:          {OUTPUT_FILE}")
    print(f"  Output size:          {OUTPUT_FILE.stat().st_size / 1e6:.1f} MB")
    print(f"  Elapsed:              {elapsed:.1f}s")


if __name__ == "__main__":
    main()
