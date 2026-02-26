"""
Signal 2 - Step 1: Build Price History

Reconstructs per-market price history from the raw trade stream.
For each market, computes time-weighted average price (TWAP) in fixed time
buckets -- a simple mean of trade prices within each bucket.

Input:  ../../../historical-data/processed/trades.csv  (151M rows, 33 GB)
Output: output/price_history.parquet

Columns produced:
  - market_id (i64)
  - bucket_start (datetime)
  - bucket_end (datetime)
  - avg_price (f64)       simple mean of trade prices in this bucket (TWAP)
  - num_trades (u32)      number of trades in this bucket
  - total_volume (f64)    total USD volume in this bucket

Memory: Processes trades.csv in chunks via polars read_csv_batched.
        Accumulates bucket aggregates incrementally in a dict, then
        flushes to parquet at the end.

Usage:
  cd scripts/analysis/signal2-timing
  uv run python build_price_history.py
"""

import time
from pathlib import Path
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
PRICE_BUCKET_MINUTES = 5          # Granularity of price history buckets
CHUNK_SIZE = 2_000_000            # Rows per chunk when reading trades.csv
FLUSH_EVERY_N_CHUNKS = 50         # Flush accumulated data to reduce memory

# ---------------------------------------------------------------------------
# Paths (relative to this script's directory)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
TRADES_CSV = SCRIPT_DIR / ".." / ".." / ".." / "historical-data" / "processed" / "trades.csv"
OUTPUT_DIR = SCRIPT_DIR / "output"
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


def main() -> None:
    print(f"[build_price_history] Starting...")
    print(f"  Trades CSV : {TRADES_CSV}")
    print(f"  Output     : {OUTPUT_FILE}")
    print(f"  Bucket size: {PRICE_BUCKET_MINUTES} minutes")
    print(f"  Chunk size : {CHUNK_SIZE:,} rows")
    print()

    if not TRADES_CSV.exists():
        raise FileNotFoundError(f"Trades CSV not found: {TRADES_CSV}")

    t0 = time.time()

    # We'll read in batches and accumulate partial aggregates.
    # Each partial aggregate has (market_id, bucket_start) -> (price_sum, total_volume, num_trades)
    # We periodically combine partials to keep memory bounded.

    reader = pl.read_csv_batched(
        TRADES_CSV,
        batch_size=CHUNK_SIZE,
        schema_overrides={
            "timestamp": pl.String,
            "market_id": pl.Int64,
            "price": pl.Float64,
            "usd_amount": pl.Float64,
        },
        columns=["timestamp", "market_id", "price", "usd_amount"],
    )

    accumulated: list[pl.DataFrame] = []
    total_rows = 0
    chunk_count = 0

    while True:
        batches = reader.next_batches(1)
        if batches is None or len(batches) == 0:
            break

        chunk = batches[0]
        chunk_count += 1
        total_rows += len(chunk)

        # Parse timestamp string to datetime -- handle both with and without
        # fractional seconds by trying the fractional format first, then
        # falling back to the non-fractional format for any failures.
        chunk = chunk.with_columns(
            pl.col("timestamp").str.to_datetime(
                "%Y-%m-%dT%H:%M:%S%.f", strict=False
            ).alias("timestamp_parsed"),
        )
        # For rows where fractional-seconds parse failed, try without fractional
        chunk = chunk.with_columns(
            pl.when(pl.col("timestamp_parsed").is_null())
            .then(pl.col("timestamp").str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False))
            .otherwise(pl.col("timestamp_parsed"))
            .alias("timestamp"),
        ).drop("timestamp_parsed")

        # Drop rows where price or usd_amount is null or zero (no contribution to avg)
        chunk = chunk.filter(
            pl.col("price").is_not_null()
            & pl.col("usd_amount").is_not_null()
            & (pl.col("usd_amount") > 0)
            & pl.col("timestamp").is_not_null()
        )

        partial = process_chunk(chunk, PRICE_BUCKET_MINUTES)
        accumulated.append(partial)

        if chunk_count % 10 == 0:
            elapsed = time.time() - t0
            print(f"  Processed {chunk_count} chunks ({total_rows:,} rows) in {elapsed:.1f}s")

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
