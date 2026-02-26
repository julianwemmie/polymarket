"""Process raw OrderFilled events into structured trades by joining with market metadata.

Reads goldsky/orderFilled.csv in chunks, joins each chunk with markets.csv to map
token IDs to markets, computes price/direction/amounts, and writes to trades.csv.
"""
import os
import time
from pathlib import Path
import polars as pl
from poly_utils.utils import get_markets

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data" / "ingest")))


CSV_COLUMNS = ['timestamp', 'maker', 'makerAssetId', 'makerAmountFilled',
               'taker', 'takerAssetId', 'takerAmountFilled', 'transactionHash']

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

SCHEMA_OVERRIDES = {
    "takerAssetId": pl.Utf8,
    "makerAssetId": pl.Utf8,
}

OUTPUT_COLUMNS = [
    'timestamp', 'market_id', 'maker', 'taker', 'nonusdc_side',
    'maker_direction', 'taker_direction', 'price', 'usd_amount',
    'token_amount', 'transactionHash',
]


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
    input_file: str = None,
    output_file: str = None,
    chunk_size: int = 5_000_000,
):
    if input_file is None:
        input_file = str(DATA_DIR / "goldsky" / "orderFilled.csv")
    if output_file is None:
        output_file = str(DATA_DIR / "trades.csv")
    """Process raw OrderFilled events into structured trades, in chunks.

    Args:
        input_file: Path to raw orderFilled CSV
        output_file: Path to write processed trades CSV
        chunk_size: Number of rows per chunk (default 5M, ~1.6 GB memory)
    """
    print("=" * 60)
    print("Processing Historical Trades")
    print("=" * 60)

    # Build token lookup once
    print("Building token lookup from markets.csv...")
    markets_long = build_token_lookup()
    print(f"Token mappings: {len(markets_long):,}")

    os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)

    # Remove existing output to start fresh
    if os.path.exists(output_file):
        os.remove(output_file)

    total_processed = 0
    total_start = time.time()
    chunk_num = 0

    # Use BatchedCsvReader to stream through the file sequentially
    reader = pl.read_csv_batched(
        input_file,
        batch_size=chunk_size,
        schema_overrides=CSV_SCHEMA,
    )

    while True:
        chunk_start = time.time()
        batches = reader.next_batches(1)

        if not batches:
            break

        df = batches[0]
        chunk_num += 1

        processed = process_chunk(df, markets_long)

        # Write: header only for first chunk
        if chunk_num == 1:
            processed.write_csv(output_file)
        else:
            with open(output_file, mode="a") as f:
                processed.write_csv(f, include_header=False)

        total_processed += len(processed)
        chunk_time = time.time() - chunk_start
        elapsed = time.time() - total_start
        rate = total_processed / elapsed if elapsed > 0 else 0

        print(f"  Chunk {chunk_num}: {len(processed):,} rows in {chunk_time:.1f}s "
              f"| Total: {total_processed:,} "
              f"| {rate:,.0f} rows/s")

    elapsed = time.time() - total_start
    print(f"\nDone! Processed {total_processed:,} trades in {elapsed:.1f}s")
    print(f"Output: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    process_trades()
