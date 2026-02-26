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
  For each market, slide a window of SPIKE_WINDOW_MINUTES across the sorted
  price buckets. Within each window, compare min and max avg_price. If the
  difference exceeds SPIKE_THRESHOLD, record a spike. De-duplicate overlapping
  spikes by keeping the window with the largest magnitude per market within
  a cooldown period.

Usage:
  cd scripts/analysis/signal2-timing
  uv run python detect_price_spikes.py
"""

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
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_FILE = SCRIPT_DIR / "output" / "price_history.parquet"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "price_spikes.parquet"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def detect_spikes_for_market(market_df: pl.DataFrame) -> pl.DataFrame:
    """
    Given a DataFrame of price buckets for a single market (sorted by bucket_start),
    detect all spikes exceeding the threshold within the rolling window.

    Uses a two-pass approach:
      1. Collect ALL candidate spikes (with temporal contiguity check).
      2. De-duplicate by keeping the largest magnitude spike within each cooldown window.

    Returns a DataFrame of spike records for this market.
    """
    empty_schema = {
        "market_id": pl.Int64,
        "spike_start_ts": pl.Datetime,
        "spike_end_ts": pl.Datetime,
        "price_before": pl.Float64,
        "price_after": pl.Float64,
        "magnitude_pp": pl.Float64,
        "direction": pl.String,
    }

    if len(market_df) < 2:
        return pl.DataFrame(schema=empty_schema)

    bucket_starts = market_df["bucket_start"].to_list()
    prices = market_df["avg_price"].to_list()
    num_trades_col = market_df["num_trades"].to_list()
    market_id = market_df["market_id"][0]

    window_buckets = SPIKE_WINDOW_MINUTES // PRICE_BUCKET_MINUTES
    spike_window_delta = timedelta(minutes=SPIKE_WINDOW_MINUTES)
    n = len(bucket_starts)

    # --- Pass 1: Collect ALL candidate spikes ---
    candidates = []

    for i in range(n):
        # Determine the window end: advance j until we exceed the time window
        # or run out of buckets.
        j_end = i + 1
        while j_end < n and (bucket_starts[j_end] - bucket_starts[i]) <= spike_window_delta:
            j_end += 1
        # j_end is now the first bucket outside the time window (exclusive)

        if j_end - i < 2:
            continue

        window_prices = prices[i:j_end]
        window_trades = num_trades_col[i:j_end]

        # Skip windows with insufficient trading activity
        total_trades_in_window = sum(window_trades)
        if total_trades_in_window < MIN_TRADES_IN_WINDOW:
            continue

        min_price = min(window_prices)
        max_price = max(window_prices)
        magnitude = max_price - min_price

        if magnitude < SPIKE_THRESHOLD:
            continue

        # Find positions of min and max -- use enumerate to find the first
        # occurrence by scanning, which gives us the correct temporal position.
        min_idx = None
        max_idx = None
        for k, p in enumerate(window_prices):
            if p == min_price and min_idx is None:
                min_idx = k
            if p == max_price and max_idx is None:
                max_idx = k

        if min_idx < max_idx:
            # Price went up
            direction = "up"
            spike_start_ts = bucket_starts[i + min_idx]
            spike_end_ts = bucket_starts[i + max_idx]
            price_before = min_price
            price_after = max_price
        else:
            # Price went down
            direction = "down"
            spike_start_ts = bucket_starts[i + max_idx]
            spike_end_ts = bucket_starts[i + min_idx]
            price_before = max_price
            price_after = min_price

        candidates.append({
            "market_id": market_id,
            "spike_start_ts": spike_start_ts,
            "spike_end_ts": spike_end_ts,
            "price_before": price_before,
            "price_after": price_after,
            "magnitude_pp": magnitude,
            "direction": direction,
            "_window_start_idx": i,
            "_window_end_idx": j_end - 1,
        })

    if not candidates:
        return pl.DataFrame(schema=empty_schema)

    # --- Pass 2: De-duplicate overlapping spikes ---
    # Sort candidates by magnitude descending (greedy: keep largest first).
    candidates.sort(key=lambda c: c["magnitude_pp"], reverse=True)

    cooldown_delta = timedelta(minutes=COOLDOWN_MINUTES)
    kept = []

    for candidate in candidates:
        # Unidirectional suppression: only suppress candidates that come AFTER
        # an already-kept spike (within cooldown). This prevents a later larger
        # spike from retroactively suppressing an earlier legitimate one.
        dominated = False
        for existing in kept:
            # Suppress only if the candidate starts after (or overlapping with)
            # an existing spike and within cooldown of that spike's end.
            gap = candidate["spike_start_ts"] - existing["spike_end_ts"]
            if gap >= timedelta(0) and gap < cooldown_delta:
                dominated = True
                break
            # Also suppress if they temporally overlap (candidate starts during existing)
            if existing["spike_start_ts"] <= candidate["spike_start_ts"] <= existing["spike_end_ts"]:
                dominated = True
                break
        if not dominated:
            kept.append(candidate)

    if not kept:
        return pl.DataFrame(schema=empty_schema)

    # Drop internal bookkeeping keys before creating DataFrame
    for spike in kept:
        spike.pop("_window_start_idx", None)
        spike.pop("_window_end_idx", None)

    return pl.DataFrame(kept)


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
    print("  Loading price history...")
    price_history = pl.read_parquet(INPUT_FILE)
    print(f"  Loaded {len(price_history):,} rows across "
          f"{price_history['market_id'].n_unique():,} markets")

    # Sort by market_id, bucket_start for sliding window
    price_history = price_history.sort("market_id", "bucket_start")

    # Process each market using partition_by for efficient grouping
    # (avoids O(N*M) filtering the full DataFrame per market)
    all_spikes: list[pl.DataFrame] = []
    markets_with_spikes = 0
    markets_processed = 0

    for market_df in price_history.partition_by("market_id", maintain_order=True):
        spikes_df = detect_spikes_for_market(market_df)
        markets_processed += 1

        if len(spikes_df) > 0:
            all_spikes.append(spikes_df)
            markets_with_spikes += 1

        if markets_processed % 5000 == 0:
            elapsed = time.time() - t0
            spike_count = sum(len(s) for s in all_spikes)
            print(f"  Processed {markets_processed:,} markets "
                  f"({spike_count:,} spikes found) in {elapsed:.1f}s")

    # Combine all spikes
    if not all_spikes:
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
    else:
        result = pl.concat(all_spikes)
        # Add spike_id as UInt64 to match documented schema
        result = (
            result
            .sort("spike_start_ts")
            .with_row_index("spike_id")
            .cast({"spike_id": pl.UInt64})
        )

    result.write_parquet(OUTPUT_FILE)

    elapsed = time.time() - t0
    print(f"\n[detect_price_spikes] Done.")
    print(f"  Total markets processed: {markets_processed:,}")
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
