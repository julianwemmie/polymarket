# Plan: Signal 2 — Timing Relative to Information Release

## Goal

Detect wallets that consistently enter positions shortly before price spikes. This signal is independent of accuracy — a wallet could have a mediocre win rate but still be suspicious if it repeatedly trades 30 minutes before the market moves.

## Approach

Use price spikes as a proxy for information entering the market. A jump from 20% to 80% in 30 minutes means someone with information started trading. Look backward from each spike to find which wallets entered positions in the pre-spike window. Flag wallets that appear in pre-spike windows repeatedly across different markets.

## Data Available

- `historical-data/processed/trades.csv` (151M rows, Nov 2022 – Oct 2025)
  - Columns: timestamp, market_id, maker, taker, nonusdc_side, maker_direction, taker_direction, price, usd_amount, token_amount, transactionHash
- `historical-data/markets.csv` (498K rows)

## Memory Constraint

16 GB RAM. Trades file is 33 GB. Must process in chunks or use lazy evaluation.

## Directory Structure

Everything for this signal lives in `scripts/analysis/signal2-timing/`:

```
scripts/analysis/signal2-timing/
├── build_price_history.py
├── detect_price_spikes.py
├── pre_spike_wallets.py
├── timing_score.py
└── output/
    ├── price_history.parquet
    ├── price_spikes.parquet
    ├── pre_spike_trades.parquet
    └── timing_scores.parquet
```

## Scripts

### 1. `build_price_history.py`

Reconstruct per-market price history from the trade stream.

- For each market, compute time-weighted average price in fixed intervals (e.g., 5-minute buckets)
- Columns: market_id, bucket_start, bucket_end, avg_price, num_trades, total_volume
- Processing approach: stream through trades.csv in chunks, aggregate into buckets per market
- This is a large output (every market x every 5-min bucket), but most markets have sparse activity so it compresses well in parquet
- Output: `output/price_history.parquet`

### 2. `detect_price_spikes.py`

Find significant price movements across all markets.

- Define a spike: price change > X percentage points within a rolling window
  - Start with: >30 percentage points within 30 minutes (e.g., 20% → 50%+)
  - Tunable parameters: magnitude threshold, time window
- For each spike, record:
  - market_id
  - spike_start_timestamp (when price began moving)
  - spike_end_timestamp (when price stabilized)
  - price_before, price_after
  - direction (up or down)
- Output: `output/price_spikes.parquet`

### 3. `pre_spike_wallets.py`

For each detected spike, look backward to find who traded before the move.

- For each spike, define a pre-spike window: 30 min to 4 hours before spike_start
- Find all wallets that entered positions in the correct direction during that window
  - "Correct direction" = bought before an upward spike, sold before a downward spike
- Record per wallet per spike:
  - wallet
  - market_id
  - spike_id
  - entry_timestamp (when they traded)
  - lead_time (how long before the spike)
  - usd_amount
  - entry_price
- Output: `output/pre_spike_trades.parquet`

### 4. `timing_score.py`

Aggregate pre-spike appearances per wallet across all markets.

- Per wallet:
  - num_spikes_preceded: how many different market spikes they traded before
  - num_markets: across how many distinct markets
  - avg_lead_time: average time before spike
  - total_pre_spike_usd: total capital deployed in pre-spike windows
  - hit_rate: fraction of their pre-spike trades that were in the correct direction
- Flag wallets appearing in pre-spike windows of 3+ different markets
- Compare against baseline: what fraction of random traders also appear in pre-spike windows by chance (control for active traders who trade everywhere)
- Output: `output/timing_scores.parquet`

## Tunable Parameters

| Parameter | Default | Description |
|---|---|---|
| price_bucket_size | 5 min | Granularity of price history |
| spike_threshold | 30 pp | Minimum price change to count as spike |
| spike_window | 30 min | Time window to measure spike over |
| pre_spike_start | 4 hours | Start of pre-spike lookback window |
| pre_spike_end | 30 min | End of pre-spike lookback (closest to spike) |
| min_spike_appearances | 3 | Minimum spikes preceded to flag a wallet |

## Execution Order

1. `build_price_history.py` (prerequisite — reads trades.csv)
2. `detect_price_spikes.py` (reads output/price_history.parquet)
3. `pre_spike_wallets.py` (reads output/price_spikes.parquet + trades.csv)
4. `timing_score.py` (reads output/pre_spike_trades.parquet)

## Open Questions

- **Spike definition**: 30pp in 30min is a starting heuristic. May need tuning — too sensitive catches normal market movement, too strict misses gradual insider accumulation.
- **Pre-spike window**: 30min–4hrs comes from known cases. Could widen to 24hrs for slower-moving markets.
- **False positive rate**: Active market makers will naturally appear before many spikes. Need a baseline/control to distinguish suspicious timing from high activity. Normalize by total trades per wallet.
- **Bucket size vs precision**: 5-min buckets balance storage with granularity. Could go to 1-min for more precision but larger output.
