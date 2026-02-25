# Historical Data Methods for Polymarket

## Problem

Polymarket's REST APIs are designed for the frontend, not bulk research. The Data API caps at ~1,000 trades per market, ~3,000 offset globally, and ~4,000-5,000 records per wallet. Complete historical trade data requires going around the APIs entirely.

Two viable methods exist: Goldsky subgraph scraping and direct Polygon blockchain scanning.

## Method 1: Goldsky Subgraph (poly_data)

**Repo:** [github.com/warproxxx/poly_data](https://github.com/warproxxx/poly_data)

**How it works:** Scrapes all `OrderFilled` events from Polymarket's Goldsky-hosted Orders subgraph via GraphQL pagination. Three-stage pipeline:
1. Fetch all market metadata from Gamma API → `markets.csv`
2. Scrape all OrderFilled events from subgraph → `goldsky/orderFilled.csv`
3. Join and process into structured trades → `processed/trades.csv`

**Output per trade:** timestamp, market ID, maker/taker addresses, direction (BUY/SELL), price, USD amount, token amount, tx hash

**Pre-made snapshot available** via S3 — skips days of initial collection. Incremental updates after that.

| Pros | Cons |
|---|---|
| Free, no API key or RPC node | Trusts Goldsky indexer, not blockchain directly |
| Pre-indexed — only fetches actual trades | Undocumented rate limits / fair-use policy |
| Pre-made data snapshot available | Dependent on Goldsky uptime |
| Incremental — resumes from checkpoint | Subgraph pagination quirks (skip caps at 5,000, need ID-based cursor) |
| Auto-discovers unknown markets from trades | |

**Stack:** Python, Polars/Pandas, GQL, UV package manager

**Run:** `uv run python update_all.py`

## Method 2: Polygon Blockchain Scan (poly-trade-scan)

**Repo:** [github.com/martkir/poly-trade-scan](https://github.com/martkir/poly-trade-scan)

**How it works:** Scans Polygon blocks sequentially, decodes `matchOrders` transactions (function selector `0x2287e350`) from the CTF Exchange and NegRisk CTF Exchange contracts.

2 RPC calls per block: `eth_getBlockByNumber` (timestamp) + `eth_getBlockReceipts` (all tx receipts/logs).

**Output per trade:** block_number, timestamp, tx_hash, wallet, token_id, side, maker_amount, taker_amount

**Key contracts:**

| Contract | Address |
|---|---|
| CTF Exchange (binary markets) | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| NegRisk CTF Exchange (multi-outcome) | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |

**Scan time estimates (Polygon block time ~2.2s):**

| History | Blocks | Free (50 req/s) | Paid (500 req/s) |
|---|---|---|---|
| 1 week | ~275K | ~3 hrs | ~18 min |
| 1 year | ~14.3M | ~6.7 days | ~16 hrs |
| 2 years | ~28.6M | ~13 days | ~32 hrs |
| Full (5 yrs) | ~71M | ~33 days | ~3.3 days |

| Pros | Cons |
|---|---|
| Guaranteed complete — reads blockchain directly | Requires paid RPC node (~$49/mo) for reasonable speed |
| No third-party trust — immutable source of truth | ~32 hours for 2-year backfill even on paid node |
| Every trade on every market, no gaps | Scans all blocks including empty ones (no Polymarket trades) |
| Covers CLOB era (2022+) completely | Requires separate Gamma API join for market metadata |

**RPC providers:** Alchemy Growth ($49/mo, 500+ req/s), QuickNode, Chainstack

## On-Chain Event Structure

Both methods ultimately decode the same on-chain data — `OrderFilled` events:

```solidity
event OrderFilled(
    bytes32 indexed orderHash,
    address indexed maker,
    address indexed taker,
    uint256 makerAssetId,    // token_id or 0 (USDC)
    uint256 takerAssetId,
    uint256 makerAmountFilled,
    uint256 takerAmountFilled,
    uint256 fee
);
```

**Price calculation:** if `makerAssetId == 0` (maker paid USDC), price = `makerAmountFilled / takerAmountFilled`. USDC has 6 decimals.

**Double-counting trap:** Each trade emits N+1 OrderFilled events (one per maker + one aggregate for taker where taker == exchange contract). Must filter out the taker-focused fill.

## Linking Trades to Markets

The `token_id` from on-chain trades maps to Polymarket markets via the Gamma API:
- Every market has `clobTokenIds` (index 0 = YES, index 1 = NO)
- Paginate all ~496K markets → build ~992K token_id-to-market lookup table (~2.5 min)
- Reverse lookup: `GET /markets?clob_token_ids=<token_id>`

**After the join, each trade becomes:**

| Field | Source |
|---|---|
| wallet, side, amounts, timestamp | On-chain trade data |
| market name, category | Gamma API (via token_id lookup) |
| YES or NO outcome | Gamma API (token index 0=YES, 1=NO) |
| resolution (who won) | Gamma API (`resolution` field) |
| price paid | Derived: `taker_amount / maker_amount` |
| profit/loss | Derived: price vs resolution outcome |

## Recommendation

1. **Start with poly_data snapshot** — get bulk historical trades immediately (free)
2. **Validate a sample against RPC** — pick 10-20 markets, pull trades via `eth_getLogs`, compare counts to check for subgraph gaps
3. **Use poly_data for ongoing ingestion** — incremental, free, near real-time
4. **Full RPC scan only if** validation reveals missing data

## What This Data Enables (per detection-techniques.md)

| Signal | Data Needed | Covered? |
|---|---|---|
| Repeated correct contrarian calls | wallet + market + price + resolution | Yes — trades.csv + markets.csv |
| Timing relative to information | trade timestamps + price reconstruction | Yes — reconstruct from trade stream |
| Profit concentration / implausibility | complete per-wallet history + resolution | Yes — full history per wallet |

## What This Data Does NOT Cover

- **Historical order book depth** — only filled orders are on-chain, not canceled/expired orders
- **Wallet funding chains** — requires separate USDC transfer tracing on Polygon
- **Pre-CLOB trades** (pre-2022) — different AMM contract, different decoder needed
- **Wallet identity / KYC data** — not available from any public source
