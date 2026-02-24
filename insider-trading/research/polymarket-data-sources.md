# Polymarket API & Data Sources — Comprehensive Reference

> Last updated: 2026-02-24
>
> Sources: [Polymarket Docs](https://docs.polymarket.com/), [Polymarket GitHub](https://github.com/Polymarket), [The Graph Polymarket Guide](https://thegraph.com/docs/en/subgraphs/guides/polymarket/), [PolygonScan](https://polygonscan.com/), [Dune Analytics](https://dune.com/), [Bitquery](https://docs.bitquery.io/docs/examples/polymarket-api/)

---

## Table of Contents

1. [Gamma API](#1-gamma-api)
2. [Data API](#2-data-api)
3. [CLOB API](#3-clob-api)
4. [WebSocket Feeds](#4-websocket-feeds)
5. [On-Chain Data](#5-on-chain-data)
6. [Third-Party Data Sources](#6-third-party-data-sources)
7. [Key Gotchas & Practical Notes](#7-key-gotchas--practical-notes)

---

## 1. Gamma API

**Base URL:** `https://gamma-api.polymarket.com`

**Authentication:** None required. Fully public REST API — no API key, no wallet, no auth headers.

### 1.1 Available Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/events` | List events with filtering and pagination |
| GET | `/events/{id}` | Get a specific event by ID |
| GET | `/events/slug/{slug}` | Get a specific event by slug |
| GET | `/markets` | List markets with filtering and pagination |
| GET | `/markets/{id}` | Get a specific market by ID |
| GET | `/markets/slug/{slug}` | Get a specific market by slug |
| GET | `/public-search` | Search across events, markets, and profiles |
| GET | `/tags` | Get ranked tags/categories |
| GET | `/series` | Get grouped event series |
| GET | `/sports` | Get sports metadata |
| GET | `/teams` | Get teams data |

### 1.2 Query Parameters (Events & Markets)

| Parameter | Type | Values / Notes |
|-----------|------|----------------|
| `slug` | string | Unique URL-friendly identifier |
| `tag_id` | integer | Filter by category/topic tag |
| `exclude_tag_id` | integer | Omit results with this tag |
| `order` | string | `volume_24hr`, `volume`, `liquidity`, `start_date`, `end_date`, `competitive`, `closed_time` |
| `ascending` | boolean | Sort direction (default: `false`) |
| `active` | boolean | `true` = live tradable events only |
| `closed` | boolean | Filter by resolution status |
| `limit` | integer | Results per page |
| `offset` | integer | Pagination offset (0-based) |
| `related_tags` | boolean | Include markets with related tags |

**Pagination:** Offset-based. Use `limit` + `offset`. Example: `?limit=50&offset=0` for page 1, `?limit=50&offset=50` for page 2.

### 1.3 Key Response Fields — Market Object

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal market ID |
| `question` | string | Human-readable market question |
| `conditionId` | string | Unique CTF condition identifier (used to link to CLOB/on-chain) |
| `questionId` | string | Hash used for market resolution |
| `slug` | string | URL slug (appears in polymarket.com URLs) |
| `description` | string | Full market description |
| `outcomes` | string[] | Array of outcome labels, e.g. `["Yes", "No"]` |
| `outcomePrices` | string[] | Array of prices (implied probabilities), 1:1 with outcomes |
| `clobTokenIds` | string[] | ERC1155 token IDs for trading on CLOB — one per outcome |
| `active` | boolean | Whether market is live |
| `closed` | boolean | Whether market has resolved |
| `enableOrderBook` | boolean | Whether CLOB trading is enabled |
| `volume` | string | Total lifetime volume |
| `volumeNum` | number | Numeric version of volume |
| `volume24hr` | number | 24-hour trading volume |
| `liquidity` | string | Current market liquidity |
| `liquidityNum` | number | Numeric version of liquidity |
| `liquidityClob` | number | CLOB-specific liquidity |
| `openInterest` | number | Total collateral at stake |
| `startDate` | string | Market start timestamp |
| `endDate` | string | Market end/expiry timestamp |
| `closedTime` | string | When market resolved (if closed) |
| `resolutionSource` | string | Source used for resolution |
| `category` | string | Market category |
| `marketType` | string | Market type |
| `formatType` | string | Format type |
| `fee` | string | Fee rate |
| `image` | string | Market image URL |
| `icon` | string | Market icon URL |
| `createdAt` | string | Creation timestamp |
| `updatedAt` | string | Last update timestamp |

### 1.4 Key Response Fields — Event Object

Events group related markets together. An event contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Event ID |
| `slug` | string | URL slug |
| `title` | string | Event title |
| `description` | string | Event description |
| `markets` | Market[] | Array of associated markets |
| `startDate` | string | Event start |
| `endDate` | string | Event end |
| `category` | string | Category |
| `tags` | object[] | Associated tags |

**Tip:** Fetching events via `/events` is more efficient than `/markets` because each event bundles its markets, reducing total API calls.

### 1.5 Rate Limits

| Endpoint | Limit |
|----------|-------|
| General Gamma | 4,000 req / 10s |
| `/events` | 500 req / 10s |
| `/markets` | 300 req / 10s |
| `/markets` + `/events` listing combined | 900 req / 10s |
| `/comments` | 200 req / 10s |
| `/tags` | 200 req / 10s |
| `/public-search` | 350 req / 10s |

Limits use **sliding time windows**. Excess requests are **throttled (queued/delayed)**, not dropped outright.

---

## 2. Data API

**Base URL:** `https://data-api.polymarket.com`

**Authentication:** None required for public endpoints.

### 2.1 Available Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/trades` | Fetch trades (by market and/or user) |
| GET | `/activity` | Fetch on-chain activity for a user (trades, splits, merges, redemptions) |
| GET | `/positions` | Get current positions for a user |
| GET | `/closed-positions` | Get closed/resolved positions for a user |
| GET | `/holders` | Get top holders for a specific market |
| GET | `/value` | Get total USD value of a user's positions |

### 2.2 GET /trades — Detailed

**Purpose:** Fetch trades ordered by timestamp (most recent first).

| Parameter | Type | Default | Min | Max | Notes |
|-----------|------|---------|-----|-----|-------|
| `limit` | integer | 100 | 0 | **10,000** | Max results per request |
| `offset` | integer | 0 | 0 | **10,000** | Pagination offset |
| `market` | string (CSV) | — | — | — | Comma-separated condition IDs. **Mutually exclusive** with `eventId` |
| `eventId` | string (CSV) | — | — | — | Comma-separated event IDs. **Mutually exclusive** with `market` |
| `user` | string | — | — | — | 0x-prefixed wallet address (40 hex chars) |
| `side` | string | — | — | — | `BUY` or `SELL` |
| `takerOnly` | boolean | `true` | — | — | Filter to taker trades only |
| `filterType` | string | — | — | — | `CASH` or `TOKENS` (requires `filterAmount`) |
| `filterAmount` | number | — | 0 | — | Minimum amount threshold |

**Response Fields per Trade:**

`proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`, `timestamp`, `title`, `slug`, `icon`, `eventSlug`, `outcome`, `outcomeIndex`, `name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`, `transactionHash`

#### Can You Get ALL Trades for a Market?

**Yes, but with a ceiling.** The max `limit` is 10,000 and max `offset` is 10,000, giving a theoretical maximum of **20,000 trades** via pagination (`offset=0, limit=10000` then `offset=10000, limit=10000`). For markets with more trades than this, you will need to:

1. **Use time-based filtering** via the `/activity` endpoint (which supports `start` and `end` timestamp params) to window your queries.
2. **Use on-chain data** (subgraphs or direct RPC calls) for truly complete trade history.
3. **Use the CLOB's `/data/trades`** endpoint with cursor-based pagination (`before`/`after` params).

### 2.3 GET /activity — Detailed

**Purpose:** Fetch on-chain activity for a user (ordered by timestamp).

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `user` | string | **Required** | Wallet address |
| `limit` | integer | 100 (max 500) | — |
| `offset` | integer | 0 | — |
| `market` | string (CSV) | — | Condition IDs |
| `type` | string (CSV) | — | `TRADE`, `SPLIT`, `MERGE`, `REDEEM`, `REWARD`, `CONVERSION` |
| `start` | number | — | Start timestamp (seconds) |
| `end` | number | — | End timestamp (seconds) |
| `side` | string | — | `BUY` or `SELL` (trades only) |
| `sortBy` | string | `TIMESTAMP` | `TIMESTAMP`, `TOKENS`, `CASH` |
| `sortDirection` | string | `DESC` | `ASC` or `DESC` |

**Response Fields:** `proxyWallet`, `timestamp`, `conditionId`, `type`, `size`, `usdcSize`, `transactionHash`, `price`, `asset`, `side`, `outcomeIndex`, `title`, `slug`, `icon`, `eventSlug`, `outcome`, `name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`

### 2.4 GET /positions — Detailed

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `user` | string | **Required** | Wallet address |
| `market` | string (CSV) | — | Condition IDs |
| `sizeThreshold` | number | 1.0 | Minimum position size |
| `redeemable` | boolean | — | Filter redeemable |
| `mergeable` | boolean | — | Filter mergeable |
| `title` | string | — | Market title filter |
| `limit` | integer | 100 (max 500) | — |
| `offset` | integer | 0 | — |
| `sortBy` | string | — | `TOKENS`, `CURRENT`, `INITIAL`, `CASHPNL`, `PERCENTPNL`, `TITLE`, `RESOLVING`, `PRICE` |
| `sortDirection` | string | `DESC` | `ASC` or `DESC` |

**Response Fields:** `proxyWallet`, `asset`, `conditionId`, `size`, `avgPrice`, `initialValue`, `currentValue`, `cashPnl`, `percentPnl`, `totalBought`, `realizedPnl`, `percentRealizedPnl`, `curPrice`, `redeemable`, `title`, `slug`, `icon`, `eventSlug`, `outcome`, `outcomeIndex`, `oppositeOutcome`, `oppositeAsset`, `endDate`, `negativeRisk`

### 2.5 GET /holders

| Parameter | Type | Notes |
|-----------|------|-------|
| `market` | string | **Required** — Condition ID |
| `limit` | integer | Default 100 |

**Response:** Array of token holders with `proxyWallet`, `pseudonym`, `asset`, `amount`, `outcomeIndex`, `name`, `bio`, `profileImage`, `displayUsernamePublic`

### 2.6 GET /value

| Parameter | Type | Notes |
|-----------|------|-------|
| `user` | string | **Required** — Wallet address |
| `market` | string (CSV) | Optional condition ID filter |

**Response:** `user`, `value` (total USD value)

### 2.7 Rate Limits

| Endpoint | Limit |
|----------|-------|
| General Data API | 1,000 req / 10s |
| `/trades` | 200 req / 10s |
| `/positions` | 150 req / 10s |
| `/closed-positions` | 150 req / 10s |
| Health check | 100 req / 10s |

---

## 3. CLOB API

**Base URL:** `https://clob.polymarket.com`
**Chain:** Polygon (Chain ID: 137)

### 3.1 Authentication Tiers

| Tier | Purpose | Auth Method |
|------|---------|-------------|
| **Public** | Market data, prices, order books | **None required** |
| **L1** | Derive API credentials | EIP-712 signature |
| **L2** | Place/cancel orders, query own trades | HMAC-SHA256 |

**L1 Headers:** `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE`
**L2 Headers:** `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_API_KEY`, `POLY_PASSPHRASE`

### 3.2 Public Endpoints (No Auth Required)

#### Market Discovery

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/markets` | — | Paginated list of all markets |
| GET | `/markets/{conditionId}` | conditionId | Single market object (30+ fields) |
| GET | `/simplified-markets` | — | Simplified market data |
| GET | `/sampling-markets` | — | Markets eligible for liquidity rewards |
| GET | `/sampling-simplified-markets` | — | Simplified sampling markets |

#### Pricing

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/price` | `token_id`, `side` (BUY/SELL) | Best bid or ask price |
| GET | `/prices` | `token_id[]`, `side[]` | Price map for multiple tokens |
| GET | `/midpoint` | `token_id` | Midpoint (avg of best bid/ask) |
| GET | `/midpoints` | `token_id[]` | Midpoint map for multiple tokens |
| GET | `/spread` | `token_id` | Bid-ask spread |
| GET | `/spreads` | `token_id[]` | Spread map for multiple tokens |
| GET | `/last-trade-price` | `token_id` | Most recent trade price + side |
| GET | `/last-trades-prices` | `token_id[]` | Last trades for multiple tokens |

#### Order Book

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/book` | `token_id` | Full order book (bids, asks, spreads, min order size) |
| GET | `/books` | `token_id[]`, `side` | Multiple order book summaries |

#### Historical Data

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/prices-history` | `market`, `startTs`, `endTs`, `interval`, `fidelity` | Historical price points |

**`/prices-history` Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `market` | string | Yes | Asset/token ID |
| `startTs` | number | No | Unix timestamp (start) |
| `endTs` | number | No | Unix timestamp (end) |
| `interval` | string | No | `1h`, `6h`, `1d`, `1w`, `1m`, `all`, `max` |
| `fidelity` | integer | No | Data accuracy in minutes (default: 1) |

**Response format:**
```json
{
  "history": [
    { "t": 1700000000, "p": 0.65 }
  ]
}
```

#### Market Metadata

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/tick-size` | `token_id` | Minimum price increment |
| GET | `/neg-risk` | `token_id` | Whether market is negative risk |
| GET | `/fee-rate-bps` | `token_id` | Fee rate in basis points |

#### Utility

| Method | Path | Returns |
|--------|------|---------|
| GET | `/ok` | Health check |
| GET | `/server-time` | Current server timestamp (Unix seconds) |

### 3.3 Authenticated Endpoints (L2 — Read Only)

| Method | Path | Parameters | Returns |
|--------|------|------------|---------|
| GET | `/data/trades` | `id`, `maker_address`, `market`, `asset_id`, `before`, `after` | Your trades with cursor-based pagination |
| GET | `/data/orders` | Various filters | Your orders |

**`/data/trades` Response Fields:** `id`, `taker_order_id`, `market`, `asset_id`, `side`, `size`, `fee_rate_bps`, `price`, `status`, `match_time`, `last_update`, `outcome`, `bucket_index`, `owner`, `maker_address`, `maker_orders`, `transaction_hash`, `trader_side`

**Note:** This endpoint uses **cursor-based pagination** (`before`/`after`) rather than offset-based, which can handle arbitrarily large result sets.

### 3.4 Rate Limits

| Endpoint | Limit |
|----------|-------|
| General CLOB | 9,000 req / 10s |
| `/books` | 500–1,500 req / 10s |
| Market data endpoints | 500–1,500 req / 10s |
| Balance/allowance (GET) | 200 req / 10s |
| Balance/allowance (UPDATE) | 50 req / 10s |
| API key endpoints | 100 req / 10s |
| POST `/order` | Burst: 3,500/10s, Sustained: 36,000/10min |
| DELETE `/order` | Burst: 3,000/10s, Sustained: 30,000/10min |

---

## 4. WebSocket Feeds

### 4.1 Available Channels

| Channel | URL | Auth | Data |
|---------|-----|------|------|
| **Market** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | None | Order book, prices, trades |
| **User** | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | L2 Required | Trade lifecycle, order events |
| **Sports** | `wss://sports-api.polymarket.com/ws` | None | Live scores, game status |
| **RTDS** | `wss://ws-live-data.polymarket.com` | Optional (for user streams) | Crypto prices, comments |

### 4.2 Market Channel (Primary for Trade Monitoring)

**Connection:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
**Auth:** Not required.

**Subscription Message:**
```json
{
  "assets_ids": ["<token_id_1>", "<token_id_2>"],
  "type": "market",
  "custom_feature_enabled": true
}
```

Setting `custom_feature_enabled: true` enables additional event types.

**Keepalive:** Send `PING` every 10 seconds; server responds with `PONG`.

**Dynamic Subscription:** Modify without reconnecting:
```json
{
  "assets_ids": ["<new_token_id>"],
  "operation": "subscribe"
}
```

#### Message Types (7 total)

| Type | Trigger | Key Fields |
|------|---------|------------|
| `book` | Initial snapshot + post-trade updates | `asset_id`, `market`, `bids[]`, `asks[]`, `timestamp`, `hash` |
| `price_change` | New/cancelled orders change best bid/ask | `market`, `price_changes[]` (asset_id, price, size, side, best_bid, best_ask), `timestamp` |
| `last_trade_price` | Maker/taker order matched | `asset_id`, `market`, `price`, `size`, `side` (BUY/SELL), `fee_rate_bps`, `timestamp` |
| `tick_size_change` | Price goes above 0.96 or below 0.04 | `asset_id`, `market`, `old_tick_size`, `new_tick_size`, `timestamp` |
| `best_bid_ask` | Best bid/ask changes (needs `custom_feature_enabled`) | `market`, `asset_id`, `best_bid`, `best_ask`, `spread`, `timestamp` |
| `new_market` | Market created (needs `custom_feature_enabled`) | `id`, `question`, `market`, `slug`, `assets_ids`, `outcomes` |
| `market_resolved` | Market resolved (needs `custom_feature_enabled`) | Same as new_market + `winning_asset_id`, `winning_outcome` |

**This is the real-time trade stream.** The `last_trade_price` event fires on every trade execution, giving you price, size, side, and timestamp in real time without polling.

### 4.3 User Channel

**Connection:** `wss://ws-subscriptions-clob.polymarket.com/ws/user`
**Auth:** L2 API credentials required.

**Subscription Message:**
```json
{
  "auth": {
    "apiKey": "your-api-key",
    "secret": "your-api-secret",
    "passphrase": "your-passphrase"
  },
  "markets": ["0x1234...condition_id"],
  "type": "user"
}
```

**Message Types:** `trade` (lifecycle updates: MATCHED -> CONFIRMED), `order` (placements/updates/cancellations)

**Keepalive:** Same as Market channel (PING every 10s).

### 4.4 Sports Channel

**Connection:** `wss://sports-api.polymarket.com/ws`
**Auth:** None.
**Subscription:** None required — all active sports data streams automatically on connect.
**Keepalive:** Server sends `ping` every 5s; respond with `pong` within 10s or get disconnected.
**Message Type:** `sport_result` (scores, periods, match status)

### 4.5 RTDS (Real-Time Data Socket)

**Connection:** `wss://ws-live-data.polymarket.com`
**Auth:** Optional `gamma_auth` for user-specific streams.
**Keepalive:** Send `PING` every 5 seconds.

**Available Streams:**
- **Crypto Prices (Binance):** Topics: `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`
- **Crypto Prices (Chainlink):** Topics: `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd`
- **Comments:** `comment_created`, `comment_removed`, `reaction_created`, `reaction_removed`

**Subscription format:**
```json
{
  "action": "subscribe",
  "subscriptions": [
    { "topic": "btcusdt", "type": "price" }
  ]
}
```

**Message format:**
```json
{
  "topic": "btcusdt",
  "type": "price",
  "timestamp": 1700000000000,
  "payload": { "symbol": "btcusdt", "value": 97500.50 }
}
```

---

## 5. On-Chain Data

### 5.1 Core Smart Contracts (Polygon Network)

| Contract | Address | Purpose |
|----------|---------|---------|
| **Conditional Tokens (CTF)** | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | Core ERC-1155 token minting/management |
| **CTF Exchange** | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | Binary market (YES/NO) order settlement |
| **NegRisk CTF Exchange** | `0xC5d563A36AE78145C45a50134d48A1215220f80a` | Multi-outcome market settlement |
| **NegRiskAdapter** | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` | Multi-outcome position structuring |
| **NegRisk Fee Module** | `0x78769d50be1763ed1ca0d5e878d93f05aabff29e` | Fee handling for neg risk markets |
| **USDC.e (Collateral)** | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | Stablecoin collateral token |
| **UMA CTF Adapter 2** | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` | Oracle for outcome resolution |
| **UMA CTF Binary Adapter** | `0xCB1822859cEF82Cd2Eb4E6276C7916e692995130` | Binary market oracle adapter |

### 5.2 On-Chain Events for Trade Data

All trade activity emits events from the CTF Exchange or NegRisk CTF Exchange contracts.

#### `OrderFilled` Event
The primary event for individual trade fills.

| Field | Type | Description |
|-------|------|-------------|
| `orderHash` | bytes32 | Unique order identifier |
| `maker` | address | Liquidity provider |
| `taker` | address | Order filler (may be exchange contract for multi-fills) |
| `makerAssetId` | uint256 | Asset provided (0 = USDC, large number = position token ID) |
| `takerAssetId` | uint256 | Asset received |
| `makerAmountFilled` | uint256 | Quantity provided |
| `takerAmountFilled` | uint256 | Quantity received |

**Interpreting direction:** If `makerAssetId == 0`, the maker is buying outcome tokens with USDC. If it is a large position ID, the maker is selling outcome tokens.

#### `OrdersMatched` Event
Summarizes multi-order matching; links buyer and seller together.

#### `PositionSplit` Event (from NegRiskAdapter/CTF)
Emitted when opposing bets create new token pairs. Both corresponding `OrderFilled` events will show `makerAssetId: 0`.

#### `PositionsMerge` Event (from NegRiskAdapter/CTF)
Emitted when opposing tokens are burned to release collateral. Both `OrderFilled` events show non-zero `makerAssetId`.

#### `PositionsConverted` Event (NegRiskAdapter only)
Converts NO tokens to YES tokens in multi-outcome markets. Uses bitmask (`indexSet`).

### 5.3 Reading Trade Data from Blockchain

**Method 1: Direct RPC Query (Python/web3.py)**
```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
# Or use a private provider (Alchemy, Infura, QuickNode)

# Filter OrderFilled logs from CTF Exchange
logs = w3.eth.get_logs({
    "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "fromBlock": block_start,
    "toBlock": block_end,
    "topics": [order_filled_topic_hash]
})
```

**Method 2: PolygonScan API**
```
https://api.polygonscan.com/api?module=logs&action=getLogs
  &address=0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
  &topic0=<OrderFilled_event_signature_hash>
  &fromBlock=<start>&toBlock=<end>
  &apikey=<YOUR_KEY>
```

**Method 3: Subgraphs** (see Section 6.1)

### 5.4 What On-Chain Data Provides That APIs Don't

- **Complete historical trade data** — no pagination limits
- **All transaction hashes** and block numbers
- **Maker and taker addresses** for every fill
- **Split, merge, and redemption events** with full detail
- **Fee module interactions**
- **Cross-market position conversions** (NegRisk)
- **Exact timing** via block timestamps
- **Raw order parameters** (salt, expiry, nonce, signature)

### 5.5 PolygonScan API

**Rate Limit:** 5 requests/second (free tier with API key)
**API Key:** Free at [polygonscan.com](https://polygonscan.com/myapikey)

Key capabilities:
- Query event logs by contract address and topic
- Get transaction details by hash
- Get internal transactions
- Get ERC-20/ERC-1155 token transfers
- Get contract ABI for decoding

**Limitation:** Log queries are capped at 1,000 results per call and limited block ranges. For bulk historical data, use subgraphs or direct node access.

---

## 6. Third-Party Data Sources

### 6.1 Subgraphs (The Graph Protocol)

Polymarket maintains **5 indexed subgraphs** hosted on Goldsky, queryable via GraphQL POST requests:

| Subgraph | Endpoint | Data |
|----------|----------|------|
| **Positions** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn` | User token balances |
| **Orders** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn` | Order book and trade events |
| **Activity** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn` | Splits, merges, redemptions |
| **Open Interest** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/oi-subgraph/0.0.6/gn` | Per-market and global OI |
| **PNL** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn` | User profit & loss data |

**Query example:**
```bash
curl -X POST \
  'https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn' \
  -H 'Content-Type: application/json' \
  -d '{"query": "{ orderFilledEvents(first: 10, orderBy: timestamp, orderDirection: desc) { id maker taker makerAmountFilled takerAmountFilled timestamp } }"}'
```

**The Graph Gateway (alternative access):**

Subgraph ID: `Bx1W4S7kDVxs9gC3s2G6DS8kdNBJNVhMviCtin2DiBp` (Activity)

Endpoint: `https://gateway.thegraph.com/api/{api-key}/subgraphs/id/Bx1W4S7kDVxs9gC3s2G6DS8kdNBJNVhMviCtin2DiBp`

Other subgraph IDs on The Graph:
- Main: `81Dm16JjuFSrqz813HysXoUPvzTwE7fsfPk2RTf66nyC`
- PNL: `6c58N5U4MtQE2Y8njfVrrAfRykzfqajMGeTMEvMmskVz`
- Open Interest: `ELaW6RtkbmYNmMMU6hEPsghG9Ko3EXSmiRkH855M4qfF`

**API Key:** Register at [thegraph.com/studio](https://thegraph.com/studio), connect wallet, create key. Free tier: **100k queries/month**.

**Source Code:** [github.com/Polymarket/polymarket-subgraph](https://github.com/Polymarket/polymarket-subgraph) — contains full schema and mapping logic.

### 6.2 Dune Analytics

Multiple community-maintained dashboards provide SQL-queryable Polymarket data:

| Dashboard | Author | URL | Focus |
|-----------|--------|-----|-------|
| Polymarket Activity & Volume | filarm | `dune.com/filarm/polymarket-activity` | Volume, activity metrics |
| Polymarket Overview | rchen8 | `dune.com/rchen8/polymarket` | General overview |
| Polymarket on Polygon | petertherock | `dune.com/petertherock/polymarket-on-polygon` | Polygon-specific metrics |
| Trade Activity Tracker | 0xclark_kent | `dune.com/0xclark_kent/polymarket-trade-activity-tracker` | Per-trade analysis |
| CLOB Stats | lifewillbeokay | `dune.com/lifewillbeokay/polymarket-clob-stats` | Order book statistics |
| Polymarket Overview | datadashboards | `dune.com/datadashboards/polymarket-overview` | Cross-platform comparison |
| Polymarket Analysis | lujanodera | `dune.com/lujanodera/polymarket-analysis` | General analysis |

**Dune API access:** Requires Dune account. Free tier available. SQL queries against decoded Polygon contract data.

### 6.3 Bitquery

[Bitquery](https://docs.bitquery.io/docs/examples/polymarket-api/) provides GraphQL APIs for querying Polymarket on-chain data:

- **Prediction Trades API:** Buy/sell activity, prices, volume
- **Prediction Settlements API:** Splits, merges, redemptions
- **Prediction Market API:** Market creation and lifecycle events
- **Real-time streaming** via subscriptions (Kafka streaming requires contacting support)
- **SDK:** npm package `@bitquery/polymarket-api` with TypeScript support

Access requires an API token from Bitquery.

### 6.4 Community Tools & Libraries

#### Official Polymarket Libraries
- **Python:** [py-clob-client](https://github.com/Polymarket/py-clob-client)
- **TypeScript:** [clob-client](https://github.com/Polymarket/clob-client)
- **RTDS Client:** [real-time-data-client](https://github.com/Polymarket/real-time-data-client) (TypeScript)
- **Agents Framework:** [Polymarket/agents](https://github.com/Polymarket/agents) — autonomous trading with AI

#### Community Libraries
- **Go:** [polymarket-go-gamma-client](https://github.com/ivanzzeth/polymarket-go-gamma-client)
- **OCaml:** [haut/polymarket](https://github.com/haut/polymarket) — full CLOB, Gamma, and Data API support
- **Rust RTDS:** [polymarket-rtds](https://crates.io/crates/polymarket-rtds) crate
- **Python (polymarket-apis):** [polymarket-apis on PyPI](https://pypi.org/project/polymarket-apis/)

#### Data Pipelines & Analysis
- **poly_data:** [warproxxx/poly_data](https://github.com/warproxxx/poly_data) — comprehensive pipeline for fetching, processing, and structuring Polymarket data including markets, order events, and trades
- **Polymarket Subgraph Analytics:** [PaulieB14/polymarket-subgraph-analytics](https://github.com/PaulieB14/polymarket-subgraph-analytics) — guide to building analytics using subgraphs

#### Aggregators & Tools
- **Prediction Hunt:** Cross-exchange comparison (Kalshi, Polymarket, PredictIt) with arbitrage detection
- **Polymarket JB Bot:** Open-source Telegram bot for arbitrage alerts and order book depth analysis
- **PolyTrack:** Dashboards and tracking tools

#### Curated Lists
- [Awesome-Prediction-Market-Tools](https://github.com/aarora4/Awesome-Prediction-Market-Tools)
- [Awesome-Polymarket-Tools](https://github.com/harish-garg/Awesome-Polymarket-Tools)

---

## 7. Key Gotchas & Practical Notes

### Pagination Limits
- **Data API `/trades`:** Max 10,000 limit, max 10,000 offset = ceiling of ~20,000 trades per query strategy. Use time-windowed queries or on-chain data for complete histories.
- **Data API `/activity` and `/positions`:** Max 500 per page.
- **CLOB `/data/trades`:** Uses cursor-based pagination (`before`/`after`), which can theoretically paginate through all records.

### Historical Data Depth
- **CLOB `/prices-history`:** Supports flexible time ranges via Unix timestamps. Works well for active markets. For resolved markets, data may only be available at 12+ hour granularity even for high-volume markets.
- **Gamma API:** Returns current state. No built-in historical snapshots — use `/prices-history` or on-chain data for historical analysis.
- **On-chain data:** Complete history from contract deployment. The CTF Exchange has been active since mid-2023 on Polygon.

### Rate Limit Behavior
- Polymarket uses **Cloudflare-based throttling** — excess requests are **queued/delayed**, not immediately rejected.
- Limits reset on **sliding time windows** (not fixed windows).
- Trading endpoints have **dual limits**: burst (10-second window) and sustained (10-minute window).
- HTTP 429 responses are possible when severely over limit — implement exponential backoff.

### Token ID vs Condition ID
- **Condition ID:** Identifies a market (used in Gamma API, Data API). Maps to the CTF condition.
- **Token ID (clobTokenIds):** Identifies a specific outcome token (YES or NO). Used in CLOB API for pricing, order books. Each market has 2 token IDs.
- **Asset ID / Position ID:** On-chain ERC-1155 token identifiers. Derived from condition ID + outcome index.

### Proxy Wallets
- Polymarket users trade through **proxy wallets** (smart contract wallets), not their EOA (externally owned account) directly.
- The Data API returns `proxyWallet` in responses, not the user's main address.
- Mapping proxy wallets to user profiles requires the Data API's user metadata fields.

### Multi-Outcome (NegRisk) Markets
- Markets with more than 2 outcomes use the **NegRisk CTF Exchange** contract, not the standard CTF Exchange.
- These markets have additional operations: **conversions** (swapping NO tokens for YES tokens in other outcomes).
- The `taker` field in `OrderFilled` events may show the exchange contract address instead of the actual counterparty when multi-order matching occurs.

### WebSocket Reliability
- Market channel requires `PING` every 10 seconds or connection drops.
- Sports channel is the reverse: server sends `ping` every 5 seconds, you must reply `pong` within 10 seconds.
- No guaranteed delivery — use REST APIs to backfill any gaps in WebSocket data.

### Best Strategy for Complete Trade Data

For a given market, combine sources:

1. **Real-time:** Subscribe to Market WebSocket channel (`last_trade_price` events)
2. **Recent history:** Data API `/trades` with `limit=10000`
3. **Full history:** Goldsky Orders subgraph (GraphQL queries with pagination) or direct RPC log queries against CTF Exchange / NegRisk CTF Exchange contracts
4. **Price history:** CLOB `/prices-history` for OHLC-style data
5. **Position analysis:** Data API `/positions` + `/holders` for current state; PNL subgraph for historical P&L
