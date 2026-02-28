"""Convert scraped activity events (splits/merges/redemptions) into trade rows.

Reads from:
    data/scrape/splits.csv.gz       (single-worker output)
    data/scrape/splits_chunk_*.csv.gz  (multi-worker output)
    data/scrape/merges.csv.gz
    data/scrape/merges_chunk_*.csv.gz
    data/scrape/redemptions.csv.gz
    data/scrape/redemptions_chunk_*.csv.gz

Produces synthetic trade rows matching the existing ingest schema:
    timestamp, market_id, maker, taker, nonusdc_side,
    maker_direction, taker_direction, price, usd_amount,
    token_amount, transactionHash

Output: data/ingest/activity/part_NNNN.parquet

Usage:
    uv run python -m pipeline.ingest.activity
"""

import glob as globmod
import os
import shutil
import sys
import time
from pathlib import Path

import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
SCRAPE_DIR = DATA_ROOT / "scrape"
OUTPUT_DIR = DATA_ROOT / "ingest" / "activity"

from pipeline.utils.helpers import get_markets


def _find_entity_files(entity: str) -> list[Path]:
    """Find all scrape files for an entity (single-worker + chunk files)."""
    files = []
    # Single-worker file
    single = SCRAPE_DIR / f"{entity}.csv.gz"
    if single.exists():
        files.append(single)
    # Multi-worker chunk files
    for p in sorted(globmod.glob(str(SCRAPE_DIR / f"{entity}_chunk_*.csv.gz"))):
        files.append(Path(p))
    return files


def _read_entity_files(entity_name: str, files: list[Path], schema: dict) -> pl.DataFrame:
    """Read and concatenate multiple CSV files for an entity."""
    dfs = []
    total_rows = 0
    t0 = time.time()
    for i, f in enumerate(files, 1):
        df = pl.read_csv(f, schema_overrides=schema)
        if len(df) > 0:
            dfs.append(df)
            total_rows += len(df)
        elapsed = time.time() - t0
        rate = i / elapsed if elapsed > 0 else 0
        remaining = len(files) - i
        eta = remaining / rate if rate > 0 else 0
        pct = i / len(files) * 100
        print(
            f"    [{i}/{len(files)}] {pct:.0f}% | "
            f"reading {entity_name}: {total_rows:,} rows | "
            f"{rate:.1f} files/s | ETA {int(eta)}s"
        )
    if not dfs:
        return pl.DataFrame()
    return pl.concat(dfs)


def build_condition_lookup() -> pl.DataFrame:
    """Build a conditionId -> market_id lookup from markets.csv.

    Returns DataFrame with columns: condition_id (str), market_id (str)
    """
    markets_df = get_markets()
    lookup = markets_df.select(
        pl.col("condition_id").cast(pl.Utf8),
        pl.col("id").alias("market_id").cast(pl.Utf8),
    ).filter(
        pl.col("condition_id").is_not_null() & (pl.col("condition_id") != "")
    ).unique(subset=["condition_id"], keep="first")
    return lookup


def process_splits(files: list[Path], condition_lookup: pl.DataFrame) -> pl.DataFrame:
    """Convert split events into two synthetic trade rows per split.

    A SPLIT deposits USDC and mints equal amounts of both outcome tokens.
    Price is always 0.50 (by definition of a 50/50 mint).
    """
    df = _read_entity_files("splits", files, {
        "timestamp": pl.Int64,
        "stakeholder": pl.Utf8,
        "condition": pl.Utf8,
        "amount": pl.Utf8,
    })
    if len(df) == 0:
        return pl.DataFrame()

    df = df.with_columns(
        pl.col("amount").cast(pl.Float64).alias("amount_raw"),
    )

    df = df.join(
        condition_lookup,
        left_on="condition",
        right_on="condition_id",
        how="inner",
    )

    token1_rows = df.select(
        pl.from_epoch(pl.col("timestamp"), time_unit="s").alias("timestamp"),
        pl.col("market_id").cast(pl.Int64),
        pl.lit("CONTRACT_SPLIT").alias("maker"),
        pl.col("stakeholder").alias("taker"),
        pl.lit("token1").alias("nonusdc_side"),
        pl.lit("SELL").alias("maker_direction"),
        pl.lit("BUY").alias("taker_direction"),
        pl.lit(0.50).alias("price"),
        (pl.col("amount_raw") / 10**6 / 2).alias("usd_amount"),
        (pl.col("amount_raw") / 10**6).alias("token_amount"),
        pl.col("id").alias("transactionHash"),
    )

    token2_rows = df.select(
        pl.from_epoch(pl.col("timestamp"), time_unit="s").alias("timestamp"),
        pl.col("market_id").cast(pl.Int64),
        pl.lit("CONTRACT_SPLIT").alias("maker"),
        pl.col("stakeholder").alias("taker"),
        pl.lit("token2").alias("nonusdc_side"),
        pl.lit("SELL").alias("maker_direction"),
        pl.lit("BUY").alias("taker_direction"),
        pl.lit(0.50).alias("price"),
        (pl.col("amount_raw") / 10**6 / 2).alias("usd_amount"),
        (pl.col("amount_raw") / 10**6).alias("token_amount"),
        pl.col("id").alias("transactionHash"),
    )

    return pl.concat([token1_rows, token2_rows])


def process_merges(files: list[Path], condition_lookup: pl.DataFrame) -> pl.DataFrame:
    """Convert merge events into two synthetic trade rows per merge.

    A MERGE deposits equal amounts of both outcome tokens and returns USDC.
    Price is always 0.50 (inverse of a split).
    """
    df = _read_entity_files("merges", files, {
        "timestamp": pl.Int64,
        "stakeholder": pl.Utf8,
        "condition": pl.Utf8,
        "amount": pl.Utf8,
    })
    if len(df) == 0:
        return pl.DataFrame()

    df = df.with_columns(
        pl.col("amount").cast(pl.Float64).alias("amount_raw"),
    )

    df = df.join(
        condition_lookup,
        left_on="condition",
        right_on="condition_id",
        how="inner",
    )

    token1_rows = df.select(
        pl.from_epoch(pl.col("timestamp"), time_unit="s").alias("timestamp"),
        pl.col("market_id").cast(pl.Int64),
        pl.lit("CONTRACT_MERGE").alias("maker"),
        pl.col("stakeholder").alias("taker"),
        pl.lit("token1").alias("nonusdc_side"),
        pl.lit("BUY").alias("maker_direction"),
        pl.lit("SELL").alias("taker_direction"),
        pl.lit(0.50).alias("price"),
        (pl.col("amount_raw") / 10**6 / 2).alias("usd_amount"),
        (pl.col("amount_raw") / 10**6).alias("token_amount"),
        pl.col("id").alias("transactionHash"),
    )

    token2_rows = df.select(
        pl.from_epoch(pl.col("timestamp"), time_unit="s").alias("timestamp"),
        pl.col("market_id").cast(pl.Int64),
        pl.lit("CONTRACT_MERGE").alias("maker"),
        pl.col("stakeholder").alias("taker"),
        pl.lit("token2").alias("nonusdc_side"),
        pl.lit("BUY").alias("maker_direction"),
        pl.lit("SELL").alias("taker_direction"),
        pl.lit(0.50).alias("price"),
        (pl.col("amount_raw") / 10**6 / 2).alias("usd_amount"),
        (pl.col("amount_raw") / 10**6).alias("token_amount"),
        pl.col("id").alias("transactionHash"),
    )

    return pl.concat([token1_rows, token2_rows])


def process_redemptions(files: list[Path], condition_lookup: pl.DataFrame) -> pl.DataFrame:
    """Convert redemption events into synthetic trade rows.

    A REDEEM sells winning tokens back to the contract at price 1.00 after
    market resolution. One row per redemption.

    The indexSets field indicates which outcome token is being redeemed:
    - indexSets containing "1" -> token1 (binary position 0)
    - indexSets containing "2" -> token2 (binary position 1)
    """
    df = _read_entity_files("redemptions", files, {
        "timestamp": pl.Int64,
        "redeemer": pl.Utf8,
        "condition": pl.Utf8,
        "indexSets": pl.Utf8,
        "payout": pl.Utf8,
    })
    if len(df) == 0:
        return pl.DataFrame()

    df = df.with_columns(
        pl.col("payout").cast(pl.Float64).alias("payout_raw"),
    )

    df = df.join(
        condition_lookup,
        left_on="condition",
        right_on="condition_id",
        how="inner",
    )

    df = df.with_columns(
        pl.when(pl.col("indexSets").str.contains("1"))
        .then(pl.lit("token1"))
        .when(pl.col("indexSets").str.contains("2"))
        .then(pl.lit("token2"))
        .otherwise(pl.lit("token1"))
        .alias("nonusdc_side")
    )

    rows = df.select(
        pl.from_epoch(pl.col("timestamp"), time_unit="s").alias("timestamp"),
        pl.col("market_id").cast(pl.Int64),
        pl.lit("CONTRACT_REDEEM").alias("maker"),
        pl.col("redeemer").alias("taker"),
        pl.col("nonusdc_side"),
        pl.lit("BUY").alias("maker_direction"),
        pl.lit("SELL").alias("taker_direction"),
        pl.lit(1.00).alias("price"),
        (pl.col("payout_raw") / 10**6).alias("usd_amount"),
        (pl.col("payout_raw") / 10**6).alias("token_amount"),
        pl.col("id").alias("transactionHash"),
    )

    return rows


def process_activity(output_dir: str = None):
    """Process all activity events into structured trade rows.

    Reads splits, merges, redemptions CSVs (single or chunked) and converts
    to Parquet matching the ingest/trades schema.
    """
    if output_dir is None:
        output_dir = str(OUTPUT_DIR)
    output = Path(output_dir)

    splits_files = _find_entity_files("splits")
    merges_files = _find_entity_files("merges")
    redemptions_files = _find_entity_files("redemptions")

    print("=" * 60)
    print("Processing Activity Events")
    print("=" * 60)
    print(f"  Splits:      {len(splits_files)} file(s)")
    print(f"  Merges:      {len(merges_files)} file(s)")
    print(f"  Redemptions: {len(redemptions_files)} file(s)")
    print(f"  Output:      {output}")

    if not splits_files and not merges_files and not redemptions_files:
        print("\nError: No activity data found. Run activity scraper first.")
        return

    print("\nBuilding condition_id -> market_id lookup from markets.csv...")
    condition_lookup = build_condition_lookup()
    print(f"  Condition mappings: {len(condition_lookup):,}")

    # Remove existing output to start fresh
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    total_rows = 0
    part_num = 0

    sources = [
        ("splits", splits_files, process_splits),
        ("merges", merges_files, process_merges),
        ("redemptions", redemptions_files, process_redemptions),
    ]

    # Only count sources that have files for accurate progress
    active_sources = [(n, f, p) for n, f, p in sources if f]
    total_steps = len(active_sources)

    for step, (name, files, processor) in enumerate(active_sources, 1):
        step_t0 = time.time()
        elapsed_total = step_t0 - t0
        avg_per_step = elapsed_total / (step - 1) if step > 1 else 0
        remaining_steps = total_steps - step + 1
        eta = remaining_steps * avg_per_step if step > 1 else 0
        pct = (step - 1) / total_steps * 100

        print(
            f"\n  [{step}/{total_steps}] {pct:.0f}% | "
            f"processing {name} ({len(files)} file(s)) | "
            f"ETA {int(eta)}s"
        )

        df = processor(files, condition_lookup)
        if len(df) == 0:
            print(f"    {name}: 0 rows (skipped)")
            continue

        part_num += 1
        out_path = output / f"part_{part_num:04d}.parquet"
        df.write_parquet(out_path)
        total_rows += len(df)

        step_elapsed = time.time() - step_t0
        elapsed_total = time.time() - t0
        print(
            f"    {name}: {len(df):,} trade rows -> {out_path.name} | "
            f"{step_elapsed:.1f}s this step | {elapsed_total:.1f}s total"
        )

    elapsed = time.time() - t0
    total_size = sum(f.stat().st_size for f in output.glob("*.parquet"))
    print(f"\n  [{total_steps}/{total_steps}] 100% | "
          f"{total_rows:,} activity trade rows in {elapsed:.1f}s")
    print(f"  Output: {output}/ ({part_num} part-files, {total_size / (1024**2):.1f} MB)")
    print("=" * 60)


if __name__ == "__main__":
    process_activity()
