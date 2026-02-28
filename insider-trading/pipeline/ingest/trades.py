"""Process raw OrderFilled events into structured trades.

Reads directly from scrape sources:
    data/scrape/historical.csv      — bulk archive from warproxxx/poly_data
    data/scrape/chunk_*.csv.gz      — gap scraper output (gzipped)

Joins with market metadata, computes price/direction/amounts,
and writes to data/ingest/trades/ as partitioned Parquet files.

Usage:
    uv run python -m pipeline.ingest.trades
"""

import glob
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
SCRAPE_DIR = DATA_ROOT / "scrape"
OUTPUT_DIR = DATA_ROOT / "ingest"

from pipeline.utils.helpers import get_markets

# Source paths
HISTORICAL_PATH = SCRAPE_DIR / "historical.csv"
CHUNKS_GLOB = str(SCRAPE_DIR / "chunk_*.csv.gz")
CHUNK_PATTERN = re.compile(r"chunk_(\d+)_(\d{8})_(\d{8})_part(\d+)\.csv\.gz")

CSV_SCHEMA = {
    "timestamp": pl.Int64,
    "maker": pl.Utf8,
    "makerAssetId": pl.Utf8,
    "makerAmountFilled": pl.Int64,
    "taker": pl.Utf8,
    "takerAssetId": pl.Utf8,
    "takerAmountFilled": pl.Int64,
    "transactionHash": pl.Utf8,
}

OUTPUT_COLUMNS = [
    'timestamp', 'market_id', 'maker', 'taker', 'nonusdc_side',
    'maker_direction', 'taker_direction', 'price', 'usd_amount',
    'token_amount', 'transactionHash',
]


def _sorted_chunks() -> list[Path]:
    """Get scraper chunk files sorted by worker ID then part number."""
    chunks = []
    for path in sorted(glob.glob(CHUNKS_GLOB)):
        p = Path(path)
        m = CHUNK_PATTERN.match(p.name)
        if m:
            worker_id = int(m.group(1))
            part_num = int(m.group(4))
            chunks.append((worker_id, part_num, p))
    chunks.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in chunks]


PARALLEL_READS = 8  # concurrent gzip decompression threads


def _read_chunk(path: Path) -> pl.DataFrame | None:
    """Read a single gzipped chunk file. Runs in a thread pool."""
    df = pl.read_csv(path, schema_overrides=CSV_SCHEMA)
    return df if len(df) > 0 else None


def _iter_batches(chunk_size: int):
    """Yield (source_name, DataFrame) from all scrape sources.

    Historical file is streamed in batches (can be very large).
    Chunk files are read in parallel and accumulated into ~chunk_size
    row batches before yielding, to avoid per-file overhead on thousands
    of small files.
    """
    if HISTORICAL_PATH.exists():
        reader = pl.scan_csv(
            str(HISTORICAL_PATH),
            schema_overrides=CSV_SCHEMA,
        ).collect_batches(chunk_size=chunk_size)
        for batch_num, batch in enumerate(reader, 1):
            yield (f"historical (batch {batch_num})", batch)

    # Read chunk files in parallel and accumulate into larger batches.
    chunk_files = _sorted_chunks()
    if not chunk_files:
        return

    buffer: list[pl.DataFrame] = []
    buffer_rows = 0
    chunks_in_buffer = 0

    with ThreadPoolExecutor(max_workers=PARALLEL_READS) as pool:
        # Submit reads in groups to overlap I/O with processing
        for i in range(0, len(chunk_files), PARALLEL_READS):
            group = chunk_files[i : i + PARALLEL_READS]
            results = list(pool.map(_read_chunk, group))
            for df in results:
                if df is not None:
                    buffer.append(df)
                    buffer_rows += len(df)
                    chunks_in_buffer += 1

                    if buffer_rows >= chunk_size:
                        yield (f"chunks ({chunks_in_buffer} files, {buffer_rows:,} rows)", pl.concat(buffer))
                        buffer = []
                        buffer_rows = 0
                        chunks_in_buffer = 0

    if buffer:
        yield (f"chunks ({chunks_in_buffer} files, {buffer_rows:,} rows)", pl.concat(buffer))


def build_token_lookup():
    """Build a long-format lookup table mapping token IDs to market IDs."""
    markets_df = get_markets()
    markets_df = markets_df.rename({'id': 'market_id'})

    markets_long = (
        markets_df
        .select(["market_id", "token1", "token2"])
        .unpivot(index="market_id", on=["token1", "token2"],
                 variable_name="side", value_name="asset_id")
    )
    return markets_long


def process_chunk(df, markets_long):
    """Process a chunk of raw OrderFilled events into structured trades."""
    # Convert epoch timestamp
    df = df.with_columns(
        pl.from_epoch(pl.col('timestamp'), time_unit='s').alias('timestamp')
    )

    # Identify the non-USDC asset for each trade
    df = df.with_columns(
        pl.when(pl.col("makerAssetId") != "0")
        .then(pl.col("makerAssetId"))
        .otherwise(pl.col("takerAssetId"))
        .alias("nonusdc_asset_id")
    )

    # Join on non-USDC asset to recover the market + side
    df = df.join(
        markets_long,
        left_on="nonusdc_asset_id",
        right_on="asset_id",
        how="left",
    )

    # Label maker/taker assets
    df = df.with_columns([
        pl.when(pl.col("makerAssetId") == "0").then(pl.lit("USDC")).otherwise(pl.col("side")).alias("makerAsset"),
        pl.when(pl.col("takerAssetId") == "0").then(pl.lit("USDC")).otherwise(pl.col("side")).alias("takerAsset"),
    ])

    # Normalize amounts (USDC has 6 decimals)
    df = df.with_columns([
        (pl.col("makerAmountFilled") / 10**6).alias("makerAmountFilled"),
        (pl.col("takerAmountFilled") / 10**6).alias("takerAmountFilled"),
    ])

    # Compute directions
    df = df.with_columns([
        pl.when(pl.col("takerAsset") == "USDC")
        .then(pl.lit("BUY"))
        .otherwise(pl.lit("SELL"))
        .alias("taker_direction"),

        pl.when(pl.col("takerAsset") == "USDC")
        .then(pl.lit("SELL"))
        .otherwise(pl.lit("BUY"))
        .alias("maker_direction"),
    ])

    # Compute derived fields
    df = df.with_columns([
        pl.when(pl.col("makerAsset") != "USDC")
        .then(pl.col("makerAsset"))
        .otherwise(pl.col("takerAsset"))
        .alias("nonusdc_side"),

        pl.when(pl.col("takerAsset") == "USDC")
        .then(pl.col("takerAmountFilled"))
        .otherwise(pl.col("makerAmountFilled"))
        .alias("usd_amount"),

        pl.when(pl.col("takerAsset") != "USDC")
        .then(pl.col("takerAmountFilled"))
        .otherwise(pl.col("makerAmountFilled"))
        .alias("token_amount"),

        pl.when(pl.col("takerAsset") == "USDC")
        .then(pl.col("takerAmountFilled") / pl.col("makerAmountFilled"))
        .otherwise(pl.col("makerAmountFilled") / pl.col("takerAmountFilled"))
        .cast(pl.Float64)
        .alias("price"),
    ])

    return df.select(OUTPUT_COLUMNS)


def process_trades(
    output_dir: str = None,
    chunk_size: int = 5_000_000,
):
    """Process raw OrderFilled events into structured trades.

    Reads directly from scrape sources (historical + chunks),
    processes in memory-bounded batches, writes partitioned Parquet.

    Args:
        output_dir: Directory to write Parquet part-files into
        chunk_size: Number of rows per batch for historical file (default 5M)
    """
    if output_dir is None:
        output_dir = str(OUTPUT_DIR / "trades")

    output = Path(output_dir)

    has_historical = HISTORICAL_PATH.exists()
    chunk_files = _sorted_chunks()

    print("=" * 60)
    print("Processing Trades")
    print("=" * 60)
    print(f"  Historical: {HISTORICAL_PATH.name} {'(found)' if has_historical else '(not found)'}")
    print(f"  Chunks:     {len(chunk_files)} files")
    print(f"  Output:     {output}")

    if not has_historical and not chunk_files:
        print("\nError: No source data found. Run scraping first.")
        return

    # Estimate batch count for progress (historical batches + chunk batches)
    est_hist_batches = 0
    if has_historical:
        est_hist_batches = max(HISTORICAL_PATH.stat().st_size // 200 // chunk_size, 1)
    # Chunk files get combined into ~chunk_size row batches
    est_chunk_rows = sum(cf.stat().st_size // 100 for cf in chunk_files)
    est_chunk_batches = max(est_chunk_rows // chunk_size, 1) if chunk_files else 0
    est_total_batches = est_hist_batches + est_chunk_batches

    print("\nBuilding token lookup from markets.csv...")
    markets_long = build_token_lookup()
    print(f"  Token mappings: {len(markets_long):,}")

    # Remove existing output to start fresh
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    total_processed = 0
    batch_num = 0
    t0 = time.time()

    print()
    for source_name, df in _iter_batches(chunk_size):
        batch_num += 1
        processed = process_chunk(df, markets_long)

        # Write each batch as a Parquet part-file
        processed.write_parquet(output / f"part_{batch_num:04d}.parquet")

        total_processed += len(processed)
        elapsed = time.time() - t0
        rate = total_processed / elapsed if elapsed > 0 else 0
        batch_rate = batch_num / elapsed if elapsed > 0 else 0
        remaining_batches = max(est_total_batches - batch_num, 0)
        eta = remaining_batches / batch_rate if batch_rate > 0 else 0
        eta_m, eta_s = divmod(int(eta), 60)
        pct = min(batch_num / est_total_batches * 100, 99.9) if est_total_batches > 0 else 0

        print(
            f"  [{batch_num}/{est_total_batches}] {pct:.0f}% | "
            f"{source_name}: {len(processed):,} rows | "
            f"Total: {total_processed:,} | {rate:,.0f} rows/s | "
            f"ETA {eta_m}m {eta_s:02d}s"
        )

    elapsed = time.time() - t0
    total_size = sum(f.stat().st_size for f in output.glob("*.parquet"))
    print(f"\nDone! {total_processed:,} trades in {elapsed:.1f}s")
    print(f"  Output: {output}/ ({batch_num} part-files, {total_size / (1024**3):.2f} GB)")
    print("=" * 60)


if __name__ == "__main__":
    process_trades()
