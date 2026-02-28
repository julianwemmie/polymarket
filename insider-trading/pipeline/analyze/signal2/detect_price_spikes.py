# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Signal 2 - Step 2: Detect Price Spikes

Reads the price history built in step 1 and identifies significant price
movements: any market where price moved more than SPIKE_THRESHOLD percentage
points within a SPIKE_WINDOW_MINUTES rolling window.

Input:  output/price_history.parquet
Output: output/price_spikes.parquet

Columns produced:
  - spike_id (u64)              unique identifier for each spike
  - market_id (i64)
  - spike_start_ts (datetime)   bucket where the spike window begins (low/high price)
  - spike_end_ts (datetime)     bucket where the spike window ends (high/low price)
  - price_before (f64)          price at spike_start_ts
  - price_after (f64)           price at spike_end_ts
  - magnitude_pp (f64)          absolute price change in percentage points
  - direction (str)             "up" or "down"

Strategy:
  Uses Polars' native rolling window operations to compute rolling min/max
  across all markets simultaneously in Rust, then applies a greedy cooldown
  deduplication pass on the (much smaller) candidate set.

Usage:
  cd pipeline/analyze/signal2
  uv run python detect_price_spikes.py
"""

import os
import time
from pathlib import Path
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
SPIKE_THRESHOLD = 0.30            # Minimum absolute price change (0.30 = 30 percentage points on the 0-1 probability scale)
SPIKE_WINDOW_MINUTES = 30         # Rolling window size to measure a spike
PRICE_BUCKET_MINUTES = 5          # Must match build_price_history.py
COOLDOWN_MINUTES = 60             # After detecting a spike, suppress overlapping spikes
                                  # for this many minutes in the same market
MIN_TRADES_IN_WINDOW = 2          # Minimum number of trades across the spike window
                                  # to avoid flagging low-liquidity noise

# ---------------------------------------------------------------------------
# Paths (override via POLYMARKET_DATA_DIR for Modal)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "analyze" / "signal2"
INPUT_FILE = OUTPUT_DIR / "price_history.parquet"
OUTPUT_FILE = OUTPUT_DIR / "price_spikes.parquet"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Use period 1 minute longer than window to include both endpoints
# (Polars rolling uses a half-open interval on the left)
ROLLING_PERIOD = f"{SPIKE_WINDOW_MINUTES + 1}m"


def cooldown_dedup(candidates: pl.DataFrame) -> list[dict]:
    """
    Greedy cooldown deduplication per market.

    For each market, sort candidates by magnitude descending and keep a spike
    only if no previously-kept spike overlaps or is within COOLDOWN_MINUTES.
    """
    cooldown_delta = timedelta(minutes=COOLDOWN_MINUTES)
    kept_rows: list[dict] = []

    sorted_candidates = candidates.sort(
        ["market_id", "magnitude_pp"], descending=[False, True]
    ).to_dicts()

    current_market = None
    kept_for_market: list[dict] = []

    for row in sorted_candidates:
        if row["market_id"] != current_market:
            current_market = row["market_id"]
            kept_for_market = []

        dominated = False
        for existing in kept_for_market:
            # Suppress if candidate starts after an existing spike and within cooldown
            gap = row["spike_start_ts"] - existing["spike_end_ts"]
            if gap >= timedelta(0) and gap < cooldown_delta:
                dominated = True
                break
            # Suppress if candidate starts during an existing spike
            if existing["spike_start_ts"] <= row["spike_start_ts"] <= existing["spike_end_ts"]:
                dominated = True
                break
        if not dominated:
            kept_for_market.append(row)
            kept_rows.append(row)

    return kept_rows


def detect_spikes_for_market(price_history_df: pl.DataFrame) -> pl.DataFrame:
    """Detect price spikes from a price history DataFrame.

    Accepts price history in the same schema as price_history.parquet
    (can be a single market or multiple markets).
    Returns spikes in the same schema as price_spikes.parquet.
    """
    if len(price_history_df) == 0:
        return pl.DataFrame(schema={
            "spike_id": pl.UInt64, "market_id": pl.Int64,
            "spike_start_ts": pl.Datetime("us"), "spike_end_ts": pl.Datetime("us"),
            "price_before": pl.Float64, "price_after": pl.Float64,
            "magnitude_pp": pl.Float64, "direction": pl.String,
        })

    price_history = price_history_df.sort("market_id", "bucket_start")

    candidates = (
        price_history
        .rolling("bucket_start", period=ROLLING_PERIOD, group_by="market_id")
        .agg(
            pl.col("avg_price").max().alias("roll_max"),
            pl.col("avg_price").min().alias("roll_min"),
            pl.col("num_trades").sum().alias("roll_trades"),
            pl.col("bucket_start").sort_by("avg_price", descending=True).first().alias("ts_of_max"),
            pl.col("bucket_start").sort_by("avg_price").first().alias("ts_of_min"),
        )
        .filter(
            ((pl.col("roll_max") - pl.col("roll_min")) >= SPIKE_THRESHOLD)
            & (pl.col("roll_trades") >= MIN_TRADES_IN_WINDOW)
        )
        .with_columns(
            (pl.col("roll_max") - pl.col("roll_min")).alias("magnitude_pp"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.lit("up")).otherwise(pl.lit("down")).alias("direction"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.col("roll_min")).otherwise(pl.col("roll_max")).alias("price_before"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.col("roll_max")).otherwise(pl.col("roll_min")).alias("price_after"),
            pl.min_horizontal("ts_of_min", "ts_of_max").alias("spike_start_ts"),
            pl.max_horizontal("ts_of_min", "ts_of_max").alias("spike_end_ts"),
        )
        .select("market_id", "spike_start_ts", "spike_end_ts",
                "price_before", "price_after", "magnitude_pp", "direction")
    )

    if len(candidates) == 0:
        return pl.DataFrame(schema={
            "spike_id": pl.UInt64, "market_id": pl.Int64,
            "spike_start_ts": pl.Datetime("us"), "spike_end_ts": pl.Datetime("us"),
            "price_before": pl.Float64, "price_after": pl.Float64,
            "magnitude_pp": pl.Float64, "direction": pl.String,
        })

    candidates = (
        candidates
        .sort("magnitude_pp", descending=True)
        .unique(subset=["market_id", "spike_start_ts", "spike_end_ts"], keep="first")
    )

    kept_rows = cooldown_dedup(candidates)

    if not kept_rows:
        return pl.DataFrame(schema={
            "spike_id": pl.UInt64, "market_id": pl.Int64,
            "spike_start_ts": pl.Datetime("us"), "spike_end_ts": pl.Datetime("us"),
            "price_before": pl.Float64, "price_after": pl.Float64,
            "magnitude_pp": pl.Float64, "direction": pl.String,
        })

    return (
        pl.DataFrame(kept_rows)
        .sort("spike_start_ts")
        .with_row_index("spike_id")
        .cast({"spike_id": pl.UInt64})
    )


def main() -> None:
    print(f"[detect_price_spikes] Starting...")
    print(f"  Input:            {INPUT_FILE}")
    print(f"  Output:           {OUTPUT_FILE}")
    print(f"  Spike threshold:  {SPIKE_THRESHOLD} (abs change on 0-1 scale)")
    print(f"  Spike window:     {SPIKE_WINDOW_MINUTES} min")
    print(f"  Cooldown:         {COOLDOWN_MINUTES} min")
    print(f"  Min trades/window:{MIN_TRADES_IN_WINDOW}")
    print()

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Price history not found: {INPUT_FILE}\n"
            "Run build_price_history.py first."
        )

    t0 = time.time()

    # Read price history
    print("  Loading price history...", flush=True)
    price_history = pl.read_parquet(INPUT_FILE)
    n_markets = price_history["market_id"].n_unique()
    print(f"  Loaded {len(price_history):,} rows across {n_markets:,} markets")

    # Sort by market_id, bucket_start for rolling window
    price_history = price_history.sort("market_id", "bucket_start")

    # ---- Pass 1: Vectorized rolling window spike detection ----
    print("  Computing rolling windows (vectorized)...", flush=True)
    t1 = time.time()

    candidates = (
        price_history
        .rolling("bucket_start", period=ROLLING_PERIOD, group_by="market_id")
        .agg(
            pl.col("avg_price").max().alias("roll_max"),
            pl.col("avg_price").min().alias("roll_min"),
            pl.col("num_trades").sum().alias("roll_trades"),
            # First occurrence of max price (stable sort: earliest timestamp wins ties)
            pl.col("bucket_start").sort_by("avg_price", descending=True).first().alias("ts_of_max"),
            # First occurrence of min price
            pl.col("bucket_start").sort_by("avg_price").first().alias("ts_of_min"),
        )
        .filter(
            ((pl.col("roll_max") - pl.col("roll_min")) >= SPIKE_THRESHOLD)
            & (pl.col("roll_trades") >= MIN_TRADES_IN_WINDOW)
        )
        .with_columns(
            (pl.col("roll_max") - pl.col("roll_min")).alias("magnitude_pp"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.lit("up")).otherwise(pl.lit("down")).alias("direction"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.col("roll_min")).otherwise(pl.col("roll_max")).alias("price_before"),
            pl.when(pl.col("ts_of_min") < pl.col("ts_of_max"))
            .then(pl.col("roll_max")).otherwise(pl.col("roll_min")).alias("price_after"),
            pl.min_horizontal("ts_of_min", "ts_of_max").alias("spike_start_ts"),
            pl.max_horizontal("ts_of_min", "ts_of_max").alias("spike_end_ts"),
        )
        .select("market_id", "spike_start_ts", "spike_end_ts",
                "price_before", "price_after", "magnitude_pp", "direction")
    )

    elapsed_rolling = time.time() - t1
    n_candidate_markets = candidates["market_id"].n_unique()
    print(f"  Rolling windows: {elapsed_rolling:.1f}s | "
          f"{len(candidates):,} candidate windows across {n_candidate_markets:,} markets",
          flush=True)

    if len(candidates) == 0:
        print("\nNo spikes detected. Try lowering SPIKE_THRESHOLD.")
        result = pl.DataFrame(schema={
            "spike_id": pl.UInt64,
            "market_id": pl.Int64,
            "spike_start_ts": pl.Datetime,
            "spike_end_ts": pl.Datetime,
            "price_before": pl.Float64,
            "price_after": pl.Float64,
            "magnitude_pp": pl.Float64,
            "direction": pl.String,
        })
        result.write_parquet(OUTPUT_FILE)
        return

    # ---- Deduplicate identical spikes from overlapping windows ----
    print("  Deduplicating identical candidates...", flush=True)
    t2 = time.time()

    candidates = (
        candidates
        .sort("magnitude_pp", descending=True)
        .unique(subset=["market_id", "spike_start_ts", "spike_end_ts"], keep="first")
    )

    print(f"  Unique candidates: {len(candidates):,} ({time.time() - t2:.1f}s)", flush=True)

    # ---- Pass 2: Cooldown dedup per market ----
    print("  Applying cooldown dedup...", flush=True)
    t3 = time.time()

    kept_rows = cooldown_dedup(candidates)

    elapsed_dedup = time.time() - t3
    print(f"  Cooldown dedup: {elapsed_dedup:.1f}s | {len(kept_rows):,} spikes kept", flush=True)

    # ---- Build final result ----
    if not kept_rows:
        print("\nNo spikes survived dedup. Try lowering SPIKE_THRESHOLD or COOLDOWN_MINUTES.")
        result = pl.DataFrame(schema={
            "spike_id": pl.UInt64,
            "market_id": pl.Int64,
            "spike_start_ts": pl.Datetime,
            "spike_end_ts": pl.Datetime,
            "price_before": pl.Float64,
            "price_after": pl.Float64,
            "magnitude_pp": pl.Float64,
            "direction": pl.String,
        })
    else:
        result = (
            pl.DataFrame(kept_rows)
            .sort("spike_start_ts")
            .with_row_index("spike_id")
            .cast({"spike_id": pl.UInt64})
        )

    result.write_parquet(OUTPUT_FILE)

    elapsed = time.time() - t0
    markets_with_spikes = result["market_id"].n_unique() if len(result) > 0 else 0
    print(f"\n[detect_price_spikes] Done.")
    print(f"  Total markets:           {n_markets:,}")
    print(f"  Total spikes detected:   {len(result):,}")
    print(f"  Markets with spikes:     {markets_with_spikes:,}")
    if len(result) > 0:
        print(f"  Up spikes:               {result.filter(pl.col('direction') == 'up').height:,}")
        print(f"  Down spikes:             {result.filter(pl.col('direction') == 'down').height:,}")
        print(f"  Avg magnitude:           {result['magnitude_pp'].mean():.3f} (abs change on 0-1 scale)")
    print(f"  Output file:             {OUTPUT_FILE}")
    print(f"  Output size:             {OUTPUT_FILE.stat().st_size / 1e6:.1f} MB")
    print(f"  Elapsed:                 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
