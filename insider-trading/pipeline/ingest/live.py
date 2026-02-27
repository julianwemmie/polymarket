import warnings
warnings.filterwarnings('ignore')

import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data" / "ingest")))

sys.path.insert(0, str(PROJECT_ROOT))

import polars as pl
from pipeline.utils.helpers import get_markets, update_missing_tokens

import subprocess
import pandas as pd


def get_processed_df(df):
    markets_df = get_markets()
    markets_df = markets_df.rename({'id': 'market_id'})

    # Make markets long: (market_id, side, asset_id) where side in {"token1", "token2"}
    markets_long = (
        markets_df
        .select(["market_id", "token1", "token2"])
        .unpivot(index="market_id", on=["token1", "token2"],
                 variable_name="side", value_name="asset_id")
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

    # Label columns
    df = df.with_columns([
        pl.when(pl.col("makerAssetId") == "0").then(pl.lit("USDC")).otherwise(pl.col("side")).alias("makerAsset"),
        pl.when(pl.col("takerAssetId") == "0").then(pl.lit("USDC")).otherwise(pl.col("side")).alias("takerAsset"),
        pl.col("market_id"),
    ])

    df = df[['timestamp', 'market_id', 'maker', 'makerAsset', 'makerAmountFilled', 'taker', 'takerAsset', 'takerAmountFilled', 'transactionHash']]

    df = df.with_columns([
        (pl.col("makerAmountFilled") / 10**6).alias("makerAmountFilled"),
        (pl.col("takerAmountFilled") / 10**6).alias("takerAmountFilled"),
    ])

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
        .alias("price")
    ])

    df = df[['timestamp', 'market_id', 'maker', 'taker', 'nonusdc_side', 'maker_direction', 'taker_direction', 'price', 'usd_amount', 'token_amount', 'transactionHash']]
    return df


def process_live():
    processed_file = str(DATA_DIR / 'trades.csv')
    goldsky_file = str(DATA_DIR / 'goldsky' / 'orderFilled.csv')

    print("=" * 60)
    print("Processing Trades")
    print("=" * 60)

    last_processed = {}

    if os.path.exists(processed_file):
        print(f"Found existing processed file: {processed_file}")
        result = subprocess.run(['tail', '-n', '1', processed_file], capture_output=True, text=True)
        last_line = result.stdout.strip()
        splitted = last_line.split(',')

        last_processed['timestamp'] = pd.to_datetime(splitted[0])
        last_processed['transactionHash'] = splitted[-1]
        last_processed['maker'] = splitted[2]
        last_processed['taker'] = splitted[3]

        print(f"Resuming from: {last_processed['timestamp']}")
        print(f"   Last hash: {last_processed['transactionHash'][:16]}...")
    else:
        print("No existing processed file found - processing from beginning")

    print(f"\nReading: {goldsky_file}")

    schema_overrides = {
        "takerAssetId": pl.Utf8,
        "makerAssetId": pl.Utf8,
    }

    df = pl.scan_csv(goldsky_file, schema_overrides=schema_overrides).collect(streaming=True)
    df = df.with_columns(
        pl.from_epoch(pl.col('timestamp'), time_unit='s').alias('timestamp')
    )

    print(f"Loaded {len(df):,} rows")

    if last_processed:
        df = df.with_row_index()

        same_timestamp = df.filter(pl.col('timestamp') == last_processed['timestamp'])
        same_timestamp = same_timestamp.filter(
            (pl.col("transactionHash") == last_processed['transactionHash']) &
            (pl.col("maker") == last_processed['maker']) &
            (pl.col("taker") == last_processed['taker'])
        )

        if len(same_timestamp) > 0:
            df_process = df.filter(pl.col('index') > same_timestamp.row(0)[0])
            df_process = df_process.drop('index')
        else:
            print("Could not find resume point - processing all rows")
            df_process = df.drop('index')
    else:
        df_process = df

    print(f"Processing {len(df_process):,} new rows...")

    new_df = get_processed_df(df_process)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not os.path.isfile(processed_file):
        new_df.write_csv(processed_file)
        print(f"Created new file: {processed_file}")
    else:
        print(f"Appending {len(new_df):,} rows to {processed_file}")
        with open(processed_file, mode="a") as f:
            new_df.write_csv(f, include_header=False)

    print("=" * 60)
    print("Processing complete!")
    print("=" * 60)


if __name__ == "__main__":
    process_live()
