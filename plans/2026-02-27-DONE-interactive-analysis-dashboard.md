# Interactive Market/Wallet Analysis Dashboard

## Problem

Analysis pipeline processes the full dataset monolithically. No way to run analyses on individual markets or wallets for testing and refinement. Can't slice by timestamp because that truncates markets/wallets.

## Current State

- Dashboard reads **pre-computed** parquet files (full pipeline output)
- Every analysis script reads all data, computes everything, writes full parquet files
- Key data sizes: `wallet_positions.parquet` = 9.9 GB, trade parquets = ~23M rows
- No CLI args, env vars, or config for subsetting by market or wallet
- Signal2 data not available locally yet

## Design

A new debug/exploration dashboard page where you:
1. Select a market (search/dropdown from `markets.csv`) or wallet (text input/search)
2. Click "Analyze" — runs the relevant pipeline slice on demand
3. See results inline — metric cards, charts, tables

## Changes Required

### Layer 1: Refactor pipeline scripts into importable functions

Each script currently runs as standalone `__main__`. Needs an internal function that accepts filters:

| Script | Current | Needed |
|--------|---------|--------|
| `build_positions.py` | Reads all trades -> writes full parquet | `build_positions_for_market(market_id)` or `build_positions_for_wallet(wallet)` returning a DataFrame |
| 8 metric scripts | Read full `wallet_positions.parquet` | `compute_roi(positions_df)` etc., accepting a pre-filtered DataFrame |
| `aggregate.py` | Reads all 8 metric parquets, percentile-ranks across all wallets | Not refactored — percentile ranks are meaningless for a single entity. Debug page shows **raw metric values** instead. If pre-computed `aggregate_scores.parquet` exists, look up the wallet's score from there. |
| `build_price_history.py` | Reads all trades | `build_price_history_for_market(market_id, trades_df)` accepting pre-filtered trades |
| `detect_price_spikes.py` | Scans all markets | `detect_spikes_for_market(price_history_df)` — rolling window logic works on single-market data (drop `group_by="market_id"`) |
| `pre_spike_wallets.py` | Reads all trades + spikes | `find_pre_spike_wallets(market_id, spikes_df, trades_df)` accepting pre-filtered data |
| `timing_score.py` | Scores all wallets using global baseline stats | **Not refactored for on-demand use.** `excess_ratio` and `expected_spikes` require global dataset stats (total spikes, total markets, median market duration) that can't be derived per-entity. See "What the debug page shows instead" below. |

The `__main__` block still calls the full-dataset version so Modal pipeline is unchanged.

#### What the debug page shows instead of timing_score

The statistical scoring (`excess_ratio`, `is_flagged`) only makes sense with full-pipeline global baselines. The debug page skips `timing_score.py` entirely and instead shows the **raw observational data** from the signal2 chain:

**Market mode:** spikes detected, which wallets appeared in pre-spike windows, their lead times, USD amounts, correct-direction trades. This is the useful debugging output.

**Wallet mode:** all pre-spike trades across markets, hit rate, lead times, USD amounts. If `timing_scores.parquet` exists from a prior full run, also display the pre-computed `excess_ratio` and `is_flagged` as reference.

### Layer 2: Pre-load large datasets once

Use DuckDB's lazy parquet scanning (already used in dashboard):

- **Trade data** (~23M rows): DuckDB scans parquets on disk, filters by `market_id` or `maker`/`taker` with predicate pushdown. No full load needed.
- **`wallet_positions.parquet`** (9.9 GB): Same — DuckDB filters on disk without loading all into memory.
- **`markets.csv`**: Small, load fully for search/dropdown.

Key: datasets are NOT re-loaded each time. DuckDB scans parquets on disk and only materializes filtered rows.

Note: DuckDB returns pandas/Arrow DataFrames — pipeline functions use Polars. Convert via `pl.from_pandas()` or `pl.from_arrow()` at the boundary.

### Layer 3: New dashboard page (`pages/5_debug.py`)

**Market mode:**
- Selectbox/search to pick a market from `markets.csv`
- On submit:
  1. Query trades for that market via DuckDB
  2. `build_positions` → wallet_positions for this market
  3. Run all 8 signal1 metrics on those positions → show **raw values** (not percentile ranks)
  4. Signal2 chain: `build_price_history` → `detect_price_spikes` → `pre_spike_wallets`
- Display: market metadata, price history chart, detected spikes, all wallets that traded this market with raw metric values, pre-spike activity table

**Wallet mode:**
- Text input for wallet address
- On submit:
  1. Query `wallet_positions.parquet` for this wallet via DuckDB (no need to rebuild from trades — positions already exist)
  2. Run all 8 signal1 metrics on those positions → show **raw values**
  3. If `aggregate_scores.parquet` exists, look up pre-computed aggregate score + rank
  4. Query pre-spike trades for this wallet (if `pre_spike_trades.parquet` exists)
  5. If `timing_scores.parquet` exists, show pre-computed excess_ratio / is_flagged
- Display: raw metric cards, position table, pre-spike trades (if available), pre-computed aggregate score (if available)

### Layer 4: Caching

Use `@st.cache_data` keyed on `(market_id)` or `(wallet_address)` so re-selecting the same market/wallet is instant. Only the first computation per entity is expensive.

Pipeline functions should suppress stdout logging when called from dashboard (pass `quiet=True` or redirect prints).

## Performance Expectations

- **Single market**: Likely a few thousand trades. Signal1 + signal2 chain in seconds.
- **Single wallet**: Most wallets <1000 trades. Also fast.
- **Wallet mode signal1**: Reads from existing `wallet_positions.parquet` via DuckDB — no rebuild needed. Very fast.
- **Bottleneck**: Initial DuckDB parquet scan to extract subset — but DuckDB handles this well with predicate pushdown.

## What Stays Unchanged

- Full Modal pipeline (`modal run modal_app/analyze.py`) — untouched
- Existing 4 dashboard pages — keep reading pre-computed files
- Data format on disk — no changes to parquet schemas
- `modal_app/` wrappers — no changes
- `aggregate.py` — not refactored, debug page uses raw values instead
- `timing_score.py` — not refactored, debug page shows raw pre-spike data

## Files Touched

~11 analysis scripts (add function interface — excludes aggregate.py and timing_score.py), 1 new dashboard page, 1 update to `dashboard/lib/data.py`, 1 update to `dashboard/app.py` (add page to nav).
