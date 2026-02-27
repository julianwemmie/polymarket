# Plan: Signal 1 — Statistical Implausibility Metrics

## Goal

Implement 8 per-wallet metrics that each flag a different dimension of "too good to be true." Each metric is a standalone analysis script. No weighting or aggregation in this phase — that comes later as an optional step.

## Data Available

- `historical-data/processed/trades.csv` (151M rows, Nov 2022 – Oct 2025)
  - Columns: timestamp, market_id, maker, taker, nonusdc_side, maker_direction, taker_direction, price, usd_amount, token_amount, transactionHash
- `historical-data/markets.csv` (498K rows)
  - Columns: createdAt, id, question, answer1, answer2, neg_risk, market_slug, token1, token2, condition_id, volume, ticker, closedTime

## Memory Constraint

16 GB RAM. The trades file is 33 GB. All scripts must process in chunks or use lazy evaluation (polars scan_csv / read_csv_batched). Never load the full file into memory.

## Prerequisites

Before implementing individual metrics, we need a shared foundation:

### Step 0: Per-wallet position summary table

Build an intermediate table that aggregates trades into per-wallet, per-market positions. This is what most metrics operate on.

Each row = one wallet's net position in one market:
- wallet
- market_id
- side (token1/token2)
- avg_entry_price (volume-weighted)
- total_usd_in (total capital deployed)
- total_tokens
- num_trades
- first_trade_timestamp
- last_trade_timestamp

Then join with markets.csv to add:
- market_volume (to classify niche vs popular)
- closedTime (to identify resolved markets)
- resolution outcome (need to determine how to get this — may need to derive from final price or fetch from Gamma API)

**Open question: resolution data.** The markets.csv from Gamma API may not include resolution outcomes directly. Need to check what fields are available. Worst case, derive from trade data: if a market is closed, the final trading price converges to 0 or 1.

## Directory Structure

Everything for this signal lives in `scripts/analysis/signal1-implausibility/`:

```
scripts/analysis/signal1-implausibility/
├── build_wallet_positions.py
├── contrarian_win_rate.py
├── niche_market_accuracy.py
├── profit_factor.py
├── brier_score.py
├── position_concentration.py
├── win_streak.py
├── roi.py
├── bet_size_vs_odds.py
├── aggregate_score.py          (optional, later)
└── output/
    ├── wallet_positions.parquet
    ├── contrarian_win_rate.parquet
    ├── niche_market_accuracy.parquet
    ├── profit_factor.parquet
    ├── brier_score.parquet
    ├── position_concentration.parquet
    ├── win_streak.parquet
    ├── roi.parquet
    ├── bet_size_vs_odds.parquet
    └── aggregate_scores.parquet (optional, later)
```

## Scripts

### 1. `build_wallet_positions.py`
- Reads `historical-data/processed/trades.csv` in chunks
- Aggregates into per-wallet, per-market positions
- Joins with `historical-data/markets.csv`
- Output: `output/wallet_positions.parquet`

### 2. `contrarian_win_rate.py`
- From wallet_positions, find bets where avg_entry_price < 0.20 (bought at <20% odds)
- Check if the market resolved in favor of that side
- Per wallet: count of contrarian bets, count correct, win rate
- Flag: win rate > 60% with 5+ contrarian bets
- Output: `output/contrarian_win_rate.parquet`

### 3. `niche_market_accuracy.py`
- Define "niche" as markets with total volume below some threshold (e.g., bottom 25th percentile)
- From wallet_positions on niche markets only, compute win rate
- Per wallet: niche bet count, niche win rate
- Flag: high accuracy specifically on low-volume markets
- Output: `output/niche_market_accuracy.parquet`

### 4. `profit_factor.py`
- Per wallet across all resolved markets: sum of profits / sum of losses
- Profit per position = (resolution_payout - total_usd_in)
- Flag: profit factor > 5x with meaningful volume
- Output: `output/profit_factor.parquet`

### 5. `brier_score.py`
- For each wallet's position: compare their implied probability (entry price) against market consensus at time of entry
- Compute Brier score: mean squared error of their predictions vs outcomes
- Compare against the market's own Brier score
- Flag: wallet Brier score significantly better than market consensus
- Output: `output/brier_score.parquet`

### 6. `position_concentration.py`
- Per wallet: what fraction of their total capital went into their single largest bet
- Also: HHI (Herfindahl-Hirschman Index) across all their positions
- Flag: >50% of capital in one bet that won, especially on niche markets
- Output: `output/position_concentration.parquet`

### 7. `win_streak.py`
- Order each wallet's resolved bets by timestamp
- Find longest consecutive winning streak
- Flag: streak > 7 on resolved markets (ricosuave666 was 7/7)
- Output: `output/win_streak.parquet`

### 8. `roi.py`
- Per wallet: net profit / total capital deployed across resolved markets
- Context: flag high ROI + low bet count (5x on 3 bets vs 5x on 500)
- Output: `output/roi.parquet`

### 9. `bet_size_vs_odds.py`
- For each bet, compute: usd_amount * (1 / price - 1) = potential payout ratio
- Flag wallets placing large absolute bets (>$10K) at extreme odds (<10% or >90%)
- Normal bettors reduce size at extreme odds; insiders increase size
- Output: `output/bet_size_vs_odds.parquet`

### 10. (Optional, later) `aggregate_score.py`
- Reads all parquet files from `output/`
- Applies configurable weights
- Produces ranked list of most suspicious wallets
- Output: `output/aggregate_scores.parquet`

## Output Format

Each parquet file has at minimum: wallet, metric_value, num_bets, and any supporting detail columns.

## Execution Order

1. `build_wallet_positions.py` (prerequisite — builds the intermediate table)
2. Metrics 2-9 (independent, can run in any order or in parallel)
3. `aggregate_score.py` (optional, runs after all metrics)

## Open Questions

- **Resolution data**: How to determine which side won a resolved market? Check if markets.csv has this, or derive from trade data (final prices near 0 or 1).
- **Niche threshold**: What volume cutoff defines a "niche" market? Start with bottom 25th percentile, tune later.
- **Minimum bet count**: What's the minimum number of bets to flag a wallet? Too low = noise, too high = miss insiders who only bet once. Start with 5.
