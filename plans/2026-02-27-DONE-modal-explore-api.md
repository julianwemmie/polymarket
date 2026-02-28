# Plan: Modal Explore API for Dashboard Debug Page

## Problem
The debug/explore page (`5_debug.py`) runs on-demand analysis by loading raw trades locally via DuckDB. But trade data (~23M rows of parquet) only exists on the Modal volume — it's too large to download locally. Markets.csv is also missing locally.

## Solution
Create a new Modal app (`modal_app/explore.py`) that exposes on-demand analysis functions. The dashboard calls these remotely via `modal.Function.lookup()` instead of reading local trade files.

---

## Files to Create

### 1. `modal_app/explore.py` — New Modal app with 3 functions

**Image:** Needs both signal1 + signal2 pipeline code, plus DuckDB for predicate pushdown on trades.

```python
explore_image = _with_modal_app(
    _base
    .pip_install("polars>=1.0.0", "duckdb>=1.1")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_dir("pipeline/analyze/signal1", remote_path="/app/pipeline/analyze/signal1")
    .add_local_dir("pipeline/analyze/signal2", remote_path="/app/pipeline/analyze/signal2")
)
```

**Functions:**

#### `list_markets() -> bytes`
- Reads `/vol/scrape/markets.csv` via polars
- Returns `df.to_ipc()` bytes (zero-copy serialization, fast)
- Small payload (<1 MB), cacheable on dashboard side

#### `analyze_market(market_id: int) -> dict[str, bytes]`
- Uses DuckDB to load trades for `market_id` from `/vol/ingest/trades/*.parquet` (predicate pushdown)
- Converts to polars, then runs the full pipeline:
  1. `build_positions_for_trades(trades, markets)` → positions
  2. All 8 signal1 `compute_*()` functions → metric DataFrames
  3. `build_price_history_for_trades(trades)` → price history
  4. `detect_spikes_for_market(price_history)` → spikes
  5. `find_pre_spike_wallets(spikes, trades)` → pre-spike trades
- Returns `dict[str, bytes]` where each value is a polars `df.serialize()` (IPC bytes)
  - Keys: `positions`, `price_history`, `spikes`, `pre_spike`, `roi`, `profit_factor`, `brier_score`, `contrarian`, `niche`, `concentration`, `win_streak`, `bet_size`
- Resource config: cpu=4, memory=16384, timeout=300 (5 min — single market is fast)

#### `analyze_wallet(wallet: str) -> dict[str, bytes]`
- Uses DuckDB to load positions from `/vol/analyze/signal1/wallet_positions.parquet` for this wallet
- Runs 8 signal1 `compute_*()` functions on those positions
- Loads pre-computed aggregate score if available
- Loads pre-spike trades from signal2 parquets if available
- Loads timing score if available
- Returns `dict[str, bytes]` with keys: `positions`, `aggregate`, `timing`, `pre_spike`, + all 8 metric keys
- Resource config: cpu=4, memory=16384, timeout=300

**Serialization format:** `pl.DataFrame.serialize(format="binary")` → `pl.DataFrame.deserialize(bytes, format="binary")`. This is polars' native IPC wire format — fast, preserves schema exactly, no lossy conversions.

### 2. `dashboard/lib/modal_client.py` — Thin wrapper for calling Modal functions

Encapsulates the `modal.Function.lookup()` calls and deserialization:

```python
import modal
import polars as pl

_app_name = "polymarket-explore"

def _lookup(fn_name: str):
    return modal.Function.from_name(_app_name, fn_name)

def fetch_markets() -> pl.DataFrame:
    raw = _lookup("list_markets").remote()
    return pl.DataFrame.deserialize(raw, format="binary")

def remote_analyze_market(market_id: int) -> dict[str, pl.DataFrame]:
    raw = _lookup("analyze_market").remote(market_id)
    return {k: pl.DataFrame.deserialize(v, format="binary") for k, v in raw.items()}

def remote_analyze_wallet(wallet: str) -> dict[str, pl.DataFrame]:
    raw = _lookup("analyze_wallet").remote(wallet)
    return {k: pl.DataFrame.deserialize(v, format="binary") for k, v in raw.items()}
```

---

## Files to Modify

### 3. `modal_app/common.py` — Add explore image

Add the new image definition:

```python
explore_image = _with_modal_app(
    _base
    .pip_install("polars>=1.0.0", "duckdb>=1.1")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_dir("pipeline/analyze/signal1", remote_path="/app/pipeline/analyze/signal1")
    .add_local_dir("pipeline/analyze/signal2", remote_path="/app/pipeline/analyze/signal2")
)
```

### 4. `dashboard/pages/5_debug.py` — Switch to remote calls

**Market mode changes:**
- Replace `all_markets()` (local CSV) → `modal_client.fetch_markets()` (remote)
- Replace `trades_for_market()` + local pipeline calls → `modal_client.remote_analyze_market(market_id)` (single remote call returns all results)
- Unpack the returned dict and render exactly as before (positions, metrics, price chart, spikes, pre-spike)

**Wallet mode changes:**
- Replace `positions_for_wallet_pl()` + local signal1 compute → `modal_client.remote_analyze_wallet(wallet)`
- Unpack returned dict for positions, metrics, aggregate score, timing, pre-spike trades
- Render exactly as before

**Remove:** Local pipeline imports (build_positions_for_trades, compute_*, signal2 functions). The debug page no longer calls pipeline code directly — Modal does it.

**Keep:** `search_wallets()` from `lib/data.py` — this reads from `aggregate_scores.parquet` which exists locally.

### 5. `dashboard/lib/data.py` — Minor cleanup

Remove `trades_for_market()` and `TRADES_DIR` export (no longer needed by debug page). Keep everything else — the other pages still read local parquets.

### 6. `pyproject.toml` — Add modal to dashboard deps

The dashboard now needs `modal` to call remote functions:

```toml
dashboard = [
    "streamlit>=1.40",
    "duckdb>=1.1",
    "plotly>=5.24",
    "modal",
]
```

---

## Deployment

Before the dashboard can call the explore functions, the Modal app needs to be deployed (persistent):

```bash
modal deploy modal_app/explore.py
```

This keeps the functions warm and callable via `Function.from_name()`. Unlike `modal run` (which exits), `modal deploy` registers them persistently.

---

## Data Flow (before vs after)

**Before (broken):**
```
Dashboard → DuckDB reads local trades/*.parquet → pipeline functions → render
                     ↑ MISSING locally
```

**After:**
```
Dashboard → modal_client → Modal Function (remote) → DuckDB on /vol → pipeline functions → serialize → return
                                                              ↑ trades live here
```

---

## What stays local
- Pages 1-4 (overview, leaderboard, wallet detail, timing) — unchanged, read from local `analyze/signal1/*.parquet` and `analyze/signal2/*.parquet`
- `search_wallets()` — reads local `aggregate_scores.parquet` (for wallet autocomplete)

## What moves to Modal
- Market list loading (markets.csv)
- Trade loading + position building + signal1 metrics (market mode)
- Signal2 chain: price history → spike detection → pre-spike wallets (market mode)
- Wallet position loading + signal1 metrics + signal2 lookups (wallet mode)
