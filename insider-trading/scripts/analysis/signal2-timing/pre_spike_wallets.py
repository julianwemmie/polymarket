"""
Signal 2 - Step 3: Pre-Spike Wallets

For each detected price spike, looks backward into the trade stream to find
ALL wallets that traded during the pre-spike window (between
PRE_SPIKE_START_HOURS and PRE_SPIKE_END_MINUTES before the spike).

Both correct-direction and incorrect-direction trades are captured so that
downstream timing_score.py can compute hit_rate (fraction of trades in the
correct direction).

Input:
  - output/price_spikes.parquet       (from step 2)
  - ../../../historical-data/processed/trades.csv  (151M rows, 33 GB)
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
  1. Load price_spikes.parquet into memory (should be small).
  2. Build a lookup: market_id -> list of (spike_id, spike_start_ts, direction,
     pre_spike_window_start, pre_spike_window_end).
  3. Stream through trades.csv in chunks. For each chunk:
     a. Filter to only market_ids that have spikes.
     b. For matching trades, check if the trade timestamp falls within any
        pre-spike window.
     c. Emit matching (wallet, spike_id, ...) records with correct_direction flag.
  4. Concat all matches and write to parquet.

Memory: The spikes table is small. Trades are streamed in chunks.

Usage:
  cd scripts/analysis/signal2-timing
  uv run python pre_spike_wallets.py
"""

import time
from pathlib import Path
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
PRE_SPIKE_START_HOURS = 4         # How far back before spike_start to look (hours)
PRE_SPIKE_END_MINUTES = 30        # Stop looking this close to spike_start (minutes)
CHUNK_SIZE = 2_000_000            # Rows per chunk for trades.csv
MIN_USD_AMOUNT = 1.0              # Minimum trade size to consider (filter dust)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SPIKES_FILE = SCRIPT_DIR / "output" / "price_spikes.parquet"
TRADES_CSV = SCRIPT_DIR / ".." / ".." / ".." / "historical-data" / "processed" / "trades.csv"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "pre_spike_trades.parquet"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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

    A trade has a maker and a taker. We unify them into a single table:
      wallet, timestamp, market_id, side ("BUY"/"SELL"), price, usd_amount
    """
    # Maker side
    maker_records = chunk.select(
        pl.col("maker").alias("wallet"),
        "timestamp",
        "market_id",
        pl.col("maker_direction").alias("side"),
        "price",
        "usd_amount",
    )

    # Taker side
    taker_records = chunk.select(
        pl.col("taker").alias("wallet"),
        "timestamp",
        "market_id",
        pl.col("taker_direction").alias("side"),
        "price",
        "usd_amount",
    )

    return pl.concat([maker_records, taker_records])


def main() -> None:
    print(f"[pre_spike_wallets] Starting...")
    print(f"  Spikes file:      {SPIKES_FILE}")
    print(f"  Trades CSV:       {TRADES_CSV}")
    print(f"  Output:           {OUTPUT_FILE}")
    print(f"  Pre-spike window: {PRE_SPIKE_END_MINUTES}min to {PRE_SPIKE_START_HOURS}hrs before spike")
    print(f"  Min USD amount:   ${MIN_USD_AMOUNT}")
    print()

    if not SPIKES_FILE.exists():
        raise FileNotFoundError(
            f"Spikes file not found: {SPIKES_FILE}\n"
            "Run detect_price_spikes.py first."
        )
    if not TRADES_CSV.exists():
        raise FileNotFoundError(f"Trades CSV not found: {TRADES_CSV}")

    t0 = time.time()

    # Load spikes and build windows
    spikes = pl.read_parquet(SPIKES_FILE)
    print(f"  Loaded {len(spikes):,} spikes across {spikes['market_id'].n_unique():,} markets")

    output_schema = {
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

    if len(spikes) == 0:
        print("No spikes to analyze. Exiting.")
        pl.DataFrame(schema=output_schema).write_parquet(OUTPUT_FILE)
        return

    spikes = build_spike_windows(spikes)

    # Build a set of market_ids that have spikes for fast filtering
    spike_market_ids = set(spikes["market_id"].unique().to_list())

    # Build a lookup: market_id -> list of spike dicts for per-market iteration
    # This avoids the Cartesian join explosion.
    spike_windows = spikes.select(
        "spike_id", "market_id", "direction", "window_start", "window_end", "spike_start_ts",
    )
    spikes_by_market: dict[int, list[dict]] = {}
    for row in spike_windows.iter_rows(named=True):
        mid = row["market_id"]
        if mid not in spikes_by_market:
            spikes_by_market[mid] = []
        spikes_by_market[mid].append(row)

    print(f"  Up spikes: {spikes.filter(pl.col('direction') == 'up').height:,}, "
          f"Down spikes: {spikes.filter(pl.col('direction') == 'down').height:,}")

    # Stream through trades.csv and match against spike windows
    reader = pl.read_csv_batched(
        TRADES_CSV,
        batch_size=CHUNK_SIZE,
        schema_overrides={
            "timestamp": pl.String,
            "market_id": pl.Int64,
            "maker": pl.String,
            "taker": pl.String,
            "maker_direction": pl.String,
            "taker_direction": pl.String,
            "price": pl.Float64,
            "usd_amount": pl.Float64,
        },
        columns=["timestamp", "market_id", "maker", "taker",
                  "maker_direction", "taker_direction", "price", "usd_amount"],
    )

    all_matches: list[pl.DataFrame] = []
    total_rows = 0
    chunk_count = 0

    while True:
        batches = reader.next_batches(1)
        if batches is None or len(batches) == 0:
            break

        chunk = batches[0]
        chunk_count += 1
        total_rows += len(chunk)

        # Parse timestamps -- handle with and without fractional seconds
        chunk = chunk.with_columns(
            pl.col("timestamp").str.to_datetime(
                "%Y-%m-%dT%H:%M:%S%.f", strict=False
            ).alias("timestamp_parsed"),
        )
        chunk = chunk.with_columns(
            pl.when(pl.col("timestamp_parsed").is_null())
            .then(pl.col("timestamp").str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False))
            .otherwise(pl.col("timestamp_parsed"))
            .alias("timestamp"),
        ).drop("timestamp_parsed")

        # Filter to only markets with spikes and trades above minimum size
        chunk = chunk.filter(
            pl.col("market_id").is_in(list(spike_market_ids))
            & (pl.col("usd_amount") >= MIN_USD_AMOUNT)
        )

        if len(chunk) == 0:
            if chunk_count % 10 == 0:
                elapsed = time.time() - t0
                print(f"  Chunk {chunk_count} ({total_rows:,} rows) - no matches - {elapsed:.1f}s")
            continue

        # Extract ALL wallet trades (both BUY and SELL) from maker+taker
        wallet_trades = extract_all_wallet_trades(chunk)

        if len(wallet_trades) == 0:
            continue

        # Normalize side to uppercase to handle "buy"/"sell" or "BUY"/"SELL"
        wallet_trades = wallet_trades.with_columns(
            pl.col("side").str.to_uppercase(),
        )

        # Per-market iteration: for each market in this chunk, filter trades
        # for that market, then for each spike in that market, filter trades
        # by the time window. This avoids materializing the full Cartesian
        # product of trades x spikes.
        chunk_market_ids = set(wallet_trades["market_id"].unique().to_list())
        chunk_matches: list[pl.DataFrame] = []

        for mid in chunk_market_ids:
            if mid not in spikes_by_market:
                continue

            market_trades = wallet_trades.filter(pl.col("market_id") == mid)

            for spike in spikes_by_market[mid]:
                # Filter trades to the pre-spike window BEFORE any join
                spike_trades = market_trades.filter(
                    (pl.col("timestamp") >= spike["window_start"])
                    & (pl.col("timestamp") <= spike["window_end"])
                )

                if len(spike_trades) == 0:
                    continue

                # Add spike columns and compute derived fields
                spike_trades = spike_trades.with_columns(
                    pl.lit(spike["spike_id"]).alias("spike_id"),
                    pl.lit(spike["direction"]).alias("direction"),
                    pl.lit(spike["spike_start_ts"]).alias("spike_start_ts"),
                )

                spike_trades = spike_trades.with_columns(
                    ((pl.col("spike_start_ts") - pl.col("timestamp")).dt.total_minutes()).alias("lead_time_minutes"),
                    # correct_direction: BUY before up-spike or SELL before down-spike
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

                chunk_matches.append(spike_trades)

        if not chunk_matches:
            if chunk_count % 10 == 0:
                elapsed = time.time() - t0
                print(f"  Chunk {chunk_count} ({total_rows:,} rows) - 0 temporal matches - {elapsed:.1f}s")
            continue

        matched = pl.concat(chunk_matches)

        all_matches.append(matched)

        if chunk_count % 10 == 0:
            elapsed = time.time() - t0
            match_count = sum(len(m) for m in all_matches)
            print(f"  Chunk {chunk_count} ({total_rows:,} rows) - "
                  f"{match_count:,} pre-spike trades found - {elapsed:.1f}s")

    # Combine all matches
    if not all_matches:
        print("\nNo pre-spike trades found.")
        result = pl.DataFrame(schema=output_schema)
    else:
        result = pl.concat(all_matches)
        # Deduplicate: same wallet + spike_id + entry_timestamp -- if there
        # are ties (same wallet, spike, timestamp but different usd_amounts),
        # aggregate them by summing usd_amount and keeping the mean entry_price.
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
