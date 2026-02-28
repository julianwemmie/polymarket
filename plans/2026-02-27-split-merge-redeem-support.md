# Plan: Add SPLIT/MERGE/REDEEM Support to Pipeline

## Problem
The pipeline only scrapes `OrderFilled` events from the CTF Exchange orderbook subgraph.
Wallets that enter positions via SPLIT (minting both outcome tokens from USDC) have zero
cost basis in our data, causing massively inflated profit calculations. ~76k wallets affected,
5,632 false-positive flags, 43 of the top 100 "most suspicious" wallets are likely just SPLIT users.

## Overview
- Scrape `splits`, `merges`, and `redemptions` from Polymarket's **activity-subgraph** on Goldsky
- Convert them into synthetic trade rows matching the existing ingest schema
- Merge into the trades parquet so `build_positions.py` and all downstream metrics work correctly with no changes

## Step 1: New scraper for activity events
**File:** `pipeline/scrape/activity.py` (new)

Modeled on `scraper.py` but targeting the activity-subgraph:
- **URL:** `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn`
- **Entities to query:** `splits`, `merges`, `redemptions`
- **GraphQL queries** (one per entity type):
  ```graphql
  # Splits
  { splits(orderBy: timestamp, orderDirection: asc, first: 1000, where: {timestamp_gte: $start, timestamp_lt: $end}) {
      id timestamp stakeholder condition amount
  }}

  # Merges
  { merges(orderBy: timestamp, orderDirection: asc, first: 1000, where: {timestamp_gte: $start, timestamp_lt: $end}) {
      id timestamp stakeholder condition amount
  }}

  # Redemptions
  { redemptions(orderBy: timestamp, orderDirection: asc, first: 1000, where: {timestamp_gte: $start, timestamp_lt: $end}) {
      id timestamp redeemer condition indexSets payout
  }}
  ```
- **Output:** `data/scrape/splits.csv.gz`, `data/scrape/merges.csv.gz`, `data/scrape/redemptions.csv.gz`
- Reuse the same async + cursor-based pagination pattern from `scraper.py`
- Simpler than the OrderFilled scraper — start with a single-worker sequential scraper and parallelize later if needed.

## Step 2: New ingest step for activity events
**File:** `pipeline/ingest/activity.py` (new)

Reads the scraped activity CSVs and converts to synthetic trade rows matching the existing `OUTPUT_COLUMNS` schema from `trades.py`:
```
timestamp, market_id, maker, taker, nonusdc_side, maker_direction, taker_direction, price, usd_amount, token_amount, transactionHash
```

Conversion rules:

**SPLIT** (amount = USDC deposited, mints `amount` of each outcome token):
→ Two rows per split, one for each side (token1 and token2):
```
taker = stakeholder (the wallet)
maker = "CONTRACT_SPLIT"
nonusdc_side = "token1" / "token2"
taker_direction = "BUY"
maker_direction = "SELL"
price = 0.50 (always, by definition)
usd_amount = amount / 2 (half the USDC goes to each side)
token_amount = amount (full amount of tokens per side)
```

**MERGE** (amount = tokens of each side deposited, returns `amount` USDC):
→ Two rows per merge (token1 and token2):
```
taker = stakeholder
maker = "CONTRACT_MERGE"
taker_direction = "SELL"
maker_direction = "BUY"
price = 0.50
usd_amount = amount / 2
token_amount = amount
```

**REDEEM** (payout = USDC received for winning tokens after resolution):
→ One row per redemption:
```
taker = redeemer
maker = "CONTRACT_REDEEM"
taker_direction = "SELL"
maker_direction = "BUY"
price = 1.00
usd_amount = payout
token_amount = payout (winning tokens redeem 1:1)
nonusdc_side = winning side (derive from indexSets field)
```

**Key requirement:** Need a `conditionId → market_id` mapping. The markets.csv has `condition_id` and `id` (market_id) columns, so we join on that.

**Output:** `data/ingest/activity/part_NNNN.parquet` (same schema as `data/ingest/trades/`)

## Step 3: Update build_positions to read both sources
**File:** `pipeline/analyze/signal1/build_positions.py` (modify)

Change the trades loading to read from both directories:
```python
TRADES_DIR = DATA_ROOT / "ingest" / "trades"
ACTIVITY_DIR = DATA_ROOT / "ingest" / "activity"

# Read from both directories
part_files = sorted(TRADES_DIR.glob("*.parquet"))
if ACTIVITY_DIR.exists():
    part_files += sorted(ACTIVITY_DIR.glob("*.parquet"))
```

Since activity rows use the same schema, they flow through the existing `process_batch()` function. The synthetic `maker` values (`CONTRACT_SPLIT`, etc.) will appear as "wallets" in the maker perspective, but since these aren't real wallets they'll just be noise rows that never match any real analysis. The taker perspective (the actual wallet) is what matters and works correctly.

The neg-risk dedup in `process_batch()` deduplicates by `(transactionHash, wallet, market_id, side, direction)`. Activity rows won't collide with OrderFilled rows because:
- Different `maker` values (sentinel strings vs real addresses)
- Even if a SPLIT and SELL happen in the same transaction, they have different directions

**Edge case — wallet uses both SPLIT and orderbook for same market:** Both contribute to the same (wallet, market_id, side) position in Phase 2 re-aggregation. The SPLIT adds to `total_usd_in`/`tokens_bought`, the OrderFilled SELL adds to `total_usd_out`/`tokens_sold`. Correct.

Also update `build_positions_for_trades()` (the on-demand single-wallet version) to accept an optional activity DataFrame.

## Step 4: Add Modal wrapper
**File:** `modal_app/scrape.py` (modify)

Add `--task activity` option alongside `historical`, `markets`, `gap`:
```
modal run modal_app/scrape.py --task activity   # scrape splits/merges/redemptions
modal run modal_app/scrape.py --task all        # now includes activity
```

**File:** `modal_app/ingest.py` (modify)

Add activity ingest step after trades ingest.

## What does NOT change
- `profit_factor.py` — no changes
- `roi.py` — no changes
- `brier_score.py` — no changes
- All other signal1/signal2 metrics — no changes
- `dashboard/` — no changes
- The aggregate scoring — no changes

## Testing / Validation
After implementing, validate against the known wallet:
- Wallet `0x0f534113caf36b733a776614be183edc03bba7ff`
- Expected: ~61 SPLIT events ($199 USDC), ~55 TRADE events, ~1 REDEEM
- Pipeline should produce net_pnl ≈ -$0.17 (matching Polymarket's reported P/L)
- Can also spot-check other wallets via `data-api.polymarket.com/v1/leaderboard?user=...&timePeriod=ALL`

## Order of Implementation
1. `pipeline/scrape/activity.py` — new scraper
2. `pipeline/ingest/activity.py` — new ingest converter
3. `pipeline/analyze/signal1/build_positions.py` — read both sources
4. `modal_app/scrape.py` — add `--task activity`
5. `modal_app/ingest.py` — add activity ingest
6. Test with known wallet
