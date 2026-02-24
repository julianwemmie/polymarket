# Polymarket API Research - Complete Reference

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Base URLs](#base-urls)
3. [Authentication](#authentication)
4. [Gamma API (Market Metadata)](#gamma-api)
5. [CLOB API (Trading & Orderbook)](#clob-api)
6. [Data API (Positions & Activity)](#data-api)
7. [WebSocket Feeds](#websocket-feeds)
8. [Rate Limits](#rate-limits)
9. [Data Schemas](#data-schemas)
10. [SDKs & Client Libraries](#sdks)

---

## 1. Architecture Overview <a name="architecture-overview"></a>

Polymarket's API is split into four services:

| Service | Purpose | Auth Required |
|---------|---------|---------------|
| **Gamma API** | Market discovery, metadata, categories, resolution info | No |
| **CLOB API** | Orderbook, prices, trading, order management | No (read), Yes (write) |
| **Data API** | User positions, trades, activity, holders | No |
| **WebSocket** | Real-time price, orderbook, trade updates | No (market), Yes (user) |

---

## 2. Base URLs <a name="base-urls"></a>

```
Gamma API:     https://gamma-api.polymarket.com
CLOB API:      https://clob.polymarket.com
Data API:      https://data-api.polymarket.com
Bridge API:    https://bridge.polymarket.com

WebSocket (Market):  wss://ws-subscriptions-clob.polymarket.com/ws/market
WebSocket (User):    wss://ws-subscriptions-clob.polymarket.com/ws/user
WebSocket (Sports):  wss://sports-api.polymarket.com/ws
WebSocket (RTDS):    wss://ws-live-data.polymarket.com
```

Chain: **Polygon** (Chain ID: `137`)

---

## 3. Authentication <a name="authentication"></a>

### Two-Level System

**Level 1 (L1) - Private Key Signing (EIP-712)**
- Used for: Creating/deriving API credentials, signing orders locally
- The private key stays with the user; all trading is non-custodial

Required L1 Headers:
```
POLY_ADDRESS     - Polygon signer address
POLY_SIGNATURE   - EIP-712 signature
POLY_TIMESTAMP   - Current UNIX timestamp
POLY_NONCE       - Nonce (default: 0)
```

EIP-712 Domain:
- Name: ClobAuthDomain
- Version: 1
- ChainId: 137
- Message: "This message attests that I control the given wallet"

**Level 2 (L2) - HMAC-SHA256 API Credentials**
- Used for: All trading endpoint requests
- Derived from L1 signing, produces 3 values:
  - `apiKey` (UUID format)
  - `secret` (Base64-encoded, used for HMAC signing)
  - `passphrase` (random string)

Required L2 Headers:
```
POLY_ADDRESS     - Signer's Polygon address
POLY_SIGNATURE   - HMAC-SHA256 signature (using secret)
POLY_TIMESTAMP   - Current UNIX timestamp
POLY_API_KEY     - Generated apiKey value
POLY_PASSPHRASE  - Generated passphrase value
```

### Credential Endpoints

```
POST https://clob.polymarket.com/auth/api-key       # Create new API key (L1 auth)
GET  https://clob.polymarket.com/auth/derive-api-key # Derive existing key (L1 auth)
```

### Signature Types

| Type | Value | Use Case |
|------|-------|----------|
| EOA | 0 | Standard wallets (MetaMask, hardware) |
| POLY_PROXY | 1 | Magic Link / email wallets |
| GNOSIS_SAFE | 2 | Browser/embedded wallets (most common) |

### SDK Credential Flow

```python
# Python
from py_clob_client.client import ClobClient

# Step 1: Create temporary client
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,
    signature_type=2,          # GNOSIS_SAFE
    funder=PROXY_WALLET_ADDR   # from polymarket.com profile
)

# Step 2: Derive credentials (one-time)
creds = client.create_or_derive_api_creds()
# Returns: { "apiKey": "...", "secret": "...", "passphrase": "..." }

# Step 3: Set credentials for trading
client.set_api_creds(creds)
```

```typescript
// TypeScript
import { ClobClient } from "@polymarket/clob-client";

const client = new ClobClient(
    "https://clob.polymarket.com",
    137,
    signer,           // ethers Wallet
    creds,            // { key, secret, passphrase }
    2,                // signature type
    funderAddress
);
```

---

## 4. Gamma API (Market Metadata) <a name="gamma-api"></a>

Base: `https://gamma-api.polymarket.com`
Auth: **None required** - all endpoints are public.

### Events

```
GET /events                     # List events (paginated)
GET /events/{id}                # Get event by ID
GET /events/slug/{slug}         # Get event by slug
```

Query Parameters for `/events`:
| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | Unique URL identifier |
| `tag_id` | integer | Filter by category/tag |
| `exclude_tag_id` | integer | Exclude specific tag |
| `related_tags` | boolean | Include related categories |
| `active` | boolean | Filter live/tradable events |
| `closed` | boolean | Filter resolved events |
| `order` | string | Sort: `volume_24hr`, `volume`, `liquidity`, `start_date`, `end_date`, `competitive`, `closed_time` |
| `ascending` | boolean | Sort direction (default: false/descending) |
| `limit` | integer | Results per page |
| `offset` | integer | Pagination offset |

Example:
```
GET https://gamma-api.polymarket.com/events?active=true&closed=false&order=volume_24hr&limit=10&offset=0
```

### Markets

```
GET /markets                    # List markets (paginated)
GET /markets/{id}               # Get market by ID
GET /markets/slug/{slug}        # Get market by slug
```

Same query parameters as events, plus:
| Parameter | Type | Description |
|-----------|------|-------------|
| `market_ids` | string | Comma-separated market IDs |
| `token_ids` | string | Comma-separated token IDs |
| `condition_ids` | string | Comma-separated condition IDs |

Example:
```
GET https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=50&order=volume
```

### Other Gamma Endpoints

```
GET /tags                       # List all tags/categories (ranked)
GET /tags/{id}                  # Get tag by ID
GET /tags/slug/{slug}           # Get tag by slug
GET /public-search?q={query}    # Search markets, events, profiles
GET /series                     # List event series
GET /series/{id}                # Get series by ID
GET /sports                     # Sports metadata (tag IDs, images, etc.)
GET /teams                      # List teams
GET /comments?market={id}       # Comments on a market
```

---

## 5. CLOB API (Trading & Orderbook) <a name="clob-api"></a>

Base: `https://clob.polymarket.com`

### Public Endpoints (No Auth)

#### Health & Server
```
GET /ok                         # Health check
GET /server-time                # Current UNIX timestamp
```

#### Market Info
```
GET /markets                    # Paginated market list
GET /simplified-markets         # Lightweight market data
GET /sampling-markets           # Markets eligible for liquidity rewards
GET /sampling-simplified-markets
GET /market/{condition_id}      # Single market details
```

Market response includes: `condition_id`, `question`, `tokens[]`, `active`, `closed`,
`accepting_orders`, `rewards`, `maker_base_fee`, `taker_base_fee`,
`minimum_order_size`, `minimum_tick_size`

#### Orderbook
```
GET /book?token_id={id}                    # Full orderbook for one token
POST /books  (body: [{ token_id }])        # Batch orderbooks (multiple tokens)
```

OrderBook response structure:
```json
{
  "market": "condition_id",
  "asset_id": "token_id",
  "bids": [
    { "price": "0.52", "size": "150.0" },
    { "price": "0.51", "size": "200.0" }
  ],
  "asks": [
    { "price": "0.53", "size": "100.0" },
    { "price": "0.54", "size": "300.0" }
  ],
  "timestamp": "1706000000",
  "tick_size": "0.01",
  "min_order_size": "5",
  "neg_risk": false,
  "hash": "..."
}
```

#### Pricing
```
GET /price?token_id={id}&side=BUY|SELL     # Best bid/ask price
POST /prices (body or query with token IDs) # Batch prices (up to 500)
GET /midpoint?token_id={id}                 # Midpoint (avg of best bid/ask)
POST /midpoints                             # Batch midpoints (up to 500)
GET /spread?token_id={id}                   # Bid-ask spread
POST /spreads                               # Batch spreads
GET /last-trade-price?token_id={id}         # Last trade price and side
POST /last-trades-prices                    # Batch last trade prices (up to 500)
```

#### Historical Data
```
GET /prices-history?market={token_id}       # Historical price data
```

Parameters:
| Parameter | Type | Description |
|-----------|------|-------------|
| `market` | string (required) | Token ID (asset_id) |
| `startTs` | number | Unix timestamp filter (after) |
| `endTs` | number | Unix timestamp filter (before) |
| `interval` | string | `max`, `all`, `1m`, `1w`, `1d`, `6h`, `1h` |
| `fidelity` | integer | Data precision in minutes (default: 1) |

Response:
```json
{
  "history": [
    { "t": 1706000000, "p": 0.52 },
    { "t": 1706003600, "p": 0.53 }
  ]
}
```

#### Tick Size & Fees
```
GET /tick-size?token_id={id}               # Minimum price increment
GET /tick-size/{token_id}                  # Same, path parameter variant
GET /fee-rate?token_id={id}                # Fee rate in basis points
GET /fee-rate/{token_id}                   # Same, path parameter variant
GET /neg-risk?token_id={id}                # Check if neg-risk market
```

Tick sizes: `"0.1"`, `"0.01"`, `"0.001"`, `"0.0001"`

### Authenticated Endpoints (L2 Auth Required)

#### Order Placement
```
POST /order                     # Place single order
POST /orders                    # Batch orders (up to 15 per request)
```

Order parameters:
```json
{
  "tokenID": "token_id_string",
  "price": 0.50,
  "size": 10,
  "side": "BUY",
  "feeRateBps": 100,
  "nonce": 0,
  "expiration": 1706100000,
  "orderType": "GTC",
  "tickSize": "0.01",
  "negRisk": false
}
```

Order Types:
| Type | Description |
|------|-------------|
| GTC | Good-Till-Canceled (limit orders) |
| GTD | Good-Till-Date (expires at timestamp) |
| FOK | Fill-Or-Kill (full fill or cancel) |
| FAK | Fill-And-Kill (partial fill allowed, rest canceled) |

Response:
```json
{
  "success": true,
  "orderID": "uuid",
  "transactionHashes": ["0x..."],
  "status": "MATCHED",
  "takingAmount": "5.00",
  "makingAmount": "10.00"
}
```

#### Order Management
```
DELETE /order/{order_id}                    # Cancel single order
DELETE /orders                              # Batch cancel (up to 3,000 IDs)
DELETE /cancel-all                          # Cancel all open orders
DELETE /cancel-market-orders                # Cancel all orders for a market
    params: market (condition_id) or asset_id (token_id)
```

Cancel response:
```json
{
  "canceled": ["order_id_1", "order_id_2"],
  "not_canceled": { "order_id_3": "reason" }
}
```

#### Order & Trade Queries
```
GET /order/{order_id}                       # Get single order
GET /orders                                 # Get open orders (paginated)
    params: id, market, asset_id
GET /trades                                 # Get filled trades (paginated)
    params: id, maker_address, market, asset_id, before, after
GET /data/orders                            # Alternative order query
GET /data/trades                            # Alternative trade query
```

Open Order object:
```json
{
  "id": "order_uuid",
  "status": "LIVE",
  "owner": "0x...",
  "maker_address": "0x...",
  "market": "condition_id",
  "asset_id": "token_id",
  "side": "BUY",
  "original_size": "10.00",
  "size_matched": "3.00",
  "price": "0.50",
  "associated_trades": ["trade_id_1"],
  "outcome": "Yes",
  "created_at": "2024-01-01T00:00:00Z",
  "expiration": "0",
  "order_type": "GTC"
}
```

#### Balance & Allowances
```
GET /balance-allowance                      # Query token balances
    params: asset_type (COLLATERAL|CONDITIONAL), token_id
POST /update-balance-allowance              # Refresh cached balance
```

#### Notifications
```
GET /notifications                          # Account events (48-hour retention)
DELETE /notifications                       # Dismiss notifications
    params: ids (array)
```

Notification types: `1` = Order Cancellation, `2` = Order Fill, `4` = Market Resolved

#### API Key Management
```
GET  /auth/api-keys                         # List API keys
DELETE /auth/api-key                         # Delete current key
```

#### Other
```
POST /heartbeat                             # Maintain active session
POST /withdraw                              # Bridge USDC.e to other chains
```

---

## 6. Data API (Positions & Activity) <a name="data-api"></a>

Base: `https://data-api.polymarket.com`
Auth: **None required** - all endpoints are public.

### GET /positions
User's current open positions.

| Parameter | Type | Description |
|-----------|------|-------------|
| `user` | string (required) | Wallet address |
| `market` | string | ConditionId(s), comma-separated |
| `sizeThreshold` | number | Min position size (default: 1.0) |
| `redeemable` | boolean | Filter redeemable positions |
| `mergeable` | boolean | Filter mergeable positions |
| `title` | string | Market title filter |
| `limit` | integer | Max results (default 100, max 500) |
| `offset` | integer | Pagination offset |
| `sortBy` | string | TOKENS, CURRENT, INITIAL, CASHPNL, PERCENTPNL, TITLE, RESOLVING, PRICE |
| `sortDirection` | string | ASC or DESC (default DESC) |

Response fields per position:
```
proxyWallet, asset, conditionId, size, avgPrice, initialValue,
currentValue, cashPnl, percentPnl, totalBought, realizedPnl,
curPrice, redeemable, title, slug, icon, eventSlug, outcome,
outcomeIndex, oppositeOutcome, oppositeAsset, endDate, negativeRisk
```

### GET /trades
Trade history.

| Parameter | Type | Description |
|-----------|------|-------------|
| `user` | string | Wallet address |
| `market` | string | ConditionId(s), comma-separated |
| `limit` | integer | Max results (default 100, max 500) |
| `offset` | integer | Pagination offset |
| `takerOnly` | boolean | Default: true |
| `filterType` | string | CASH or TOKENS |
| `filterAmount` | number | Amount threshold |
| `side` | string | BUY or SELL |

Response fields per trade:
```
proxyWallet, side, asset, conditionId, size, price, timestamp,
title, slug, icon, eventSlug, outcome, outcomeIndex, name,
pseudonym, bio, profileImage, transactionHash
```

### GET /activity
Onchain activity (trades, splits, merges, redemptions, etc.)

| Parameter | Type | Description |
|-----------|------|-------------|
| `user` | string (required) | Wallet address |
| `market` | string | ConditionId(s), comma-separated |
| `type` | string | TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION |
| `start` | number | Start timestamp (seconds) |
| `end` | number | End timestamp (seconds) |
| `side` | string | BUY or SELL (trades only) |
| `limit` | integer | Max results (default 100, max 500) |
| `offset` | integer | Pagination offset |
| `sortBy` | string | TIMESTAMP, TOKENS, CASH |
| `sortDirection` | string | ASC or DESC |

### GET /holders
Top token holders for a market.

| Parameter | Type | Description |
|-----------|------|-------------|
| `market` | string (required) | ConditionId |
| `limit` | integer | Max holders (default 100) |

Response: Array of token objects, each with `token` and `holders[]` array containing:
```
proxyWallet, bio, asset, pseudonym, amount,
displayUsernamePublic, outcomeIndex, name, profileImage
```

### GET /value
Total USD value of user positions.

| Parameter | Type | Description |
|-----------|------|-------------|
| `user` | string (required) | Wallet address |
| `market` | string | ConditionId(s), comma-separated |

Response: `[{ "user": "0x...", "value": 1234.56 }]`

### Other Data API Endpoints
```
GET /closed-positions?user={addr}           # Historical closed positions
GET /ok                                     # Health check
GET /open-interest?market={condition_id}    # Open interest
GET /live-volume?event={event_id}           # Live trading volume
GET /total-markets?user={addr}              # Total markets traded
GET /leaderboard                            # Trader rankings
GET /accounting-snapshot                    # Download CSV ZIP
GET /profile/{address}                      # Public profile
```

---

## 7. WebSocket Feeds <a name="websocket-feeds"></a>

### Market Channel (Public, No Auth)

**URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

#### Subscribe
```json
{
  "assets_ids": ["token_id_1", "token_id_2"],
  "type": "market",
  "custom_feature_enabled": true
}
```

#### Dynamic Subscribe/Unsubscribe
```json
{
  "assets_ids": ["new_token_id"],
  "operation": "subscribe"
}
```
```json
{
  "assets_ids": ["token_id_to_remove"],
  "operation": "unsubscribe"
}
```

#### Heartbeat
Send `PING` every 10 seconds; server responds `PONG`.

#### Message Types

**`book`** - Full orderbook snapshot (on subscribe + when trades affect book)
```json
{
  "event_type": "book",
  "asset_id": "token_id",
  "market": "condition_id",
  "bids": [{ "price": "0.52", "size": "150.0" }],
  "asks": [{ "price": "0.53", "size": "100.0" }],
  "timestamp": "1706000000",
  "hash": "..."
}
```

**`price_change`** - When orders are placed or cancelled
```json
{
  "event_type": "price_change",
  "market": "condition_id",
  "price_changes": [
    {
      "asset_id": "token_id",
      "price": "0.52",
      "size": "100.0",
      "side": "BUY",
      "hash": "...",
      "best_bid": "0.52",
      "best_ask": "0.53"
    }
  ],
  "timestamp": "1706000000"
}
```
Note: `size: "0"` means that price level was removed from the book.

**`last_trade_price`** - When a trade executes
```json
{
  "event_type": "last_trade_price",
  "asset_id": "token_id",
  "market": "condition_id",
  "price": "0.52",
  "size": "10.0",
  "side": "BUY",
  "fee_rate_bps": "200",
  "timestamp": "1706000000"
}
```

**`tick_size_change`** - When price crosses 0.96 or falls below 0.04
```json
{
  "event_type": "tick_size_change",
  "asset_id": "token_id",
  "market": "condition_id",
  "old_tick_size": "0.01",
  "new_tick_size": "0.001",
  "timestamp": "1706000000"
}
```

**`best_bid_ask`** (requires `custom_feature_enabled: true`)
```json
{
  "event_type": "best_bid_ask",
  "market": "condition_id",
  "asset_id": "token_id",
  "best_bid": "0.52",
  "best_ask": "0.53",
  "spread": "0.01",
  "timestamp": "1706000000"
}
```

**`new_market`** (requires `custom_feature_enabled: true`)
```json
{
  "event_type": "new_market",
  "id": "market_id",
  "question": "Will X happen?",
  "market": "condition_id",
  "slug": "will-x-happen",
  "description": "...",
  "assets_ids": ["token_yes", "token_no"],
  "outcomes": ["Yes", "No"],
  "event_message": {
    "id": "event_id",
    "ticker": "...",
    "slug": "...",
    "title": "...",
    "description": "..."
  },
  "timestamp": "1706000000"
}
```

**`market_resolved`** (requires `custom_feature_enabled: true`)
```json
{
  "event_type": "market_resolved",
  "id": "market_id",
  "question": "...",
  "market": "condition_id",
  "winning_asset_id": "token_yes",
  "winning_outcome": "Yes",
  "timestamp": "1706000000"
}
```

### User Channel (Auth Required)

**URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/user`

#### Subscribe
```json
{
  "auth": {
    "apiKey": "your-api-key",
    "secret": "your-api-secret",
    "passphrase": "your-passphrase"
  },
  "markets": ["condition_id_1", "condition_id_2"],
  "type": "user"
}
```

#### Heartbeat
Same as market channel: `PING` every 10 seconds.

#### Message Types

**`trade`** - Trade lifecycle updates
```
Status progression: MATCHED -> MINED -> CONFIRMED
                       |         ^
                    RETRYING ----+
                       |
                     FAILED
```

Only `CONFIRMED` and `FAILED` are terminal states.

Fields: `asset_id`, `event_type`, `id`, `maker_orders[]`, `market`,
`outcome`, `owner`, `price`, `side`, `size`, `status`,
`taker_order_id`, `timestamp`, `trade_owner`

**`order`** - Order lifecycle updates
Types: `PLACEMENT`, `UPDATE` (partial fill), `CANCELLATION`

Fields: `asset_id`, `event_type`, `id`, `market`, `order_owner`,
`original_size`, `outcome`, `price`, `side`, `size_matched`,
`timestamp`, `type`

### Sports Channel (Public, No Auth)

**URL:** `wss://sports-api.polymarket.com/ws`

No subscription message required. Server sends `ping` every 5 seconds;
respond with `pong` within 10 seconds.

Event type: `sport_result` - Live game scores, periods, status.

### RTDS Channel

**URL:** `wss://ws-live-data.polymarket.com`
Auth: Optional.

---

## 8. Rate Limits <a name="rate-limits"></a>

All rate limits enforced via Cloudflare throttling (requests are delayed/queued, not rejected).
Limits reset on sliding time windows.

### Gamma API

| Endpoint | Rate Limit |
|----------|-----------|
| General catchall | 4,000 req/10s |
| `/events` | 500 req/10s |
| `/markets` | 300 req/10s |
| `/markets` + `/events` combined | 900 req/10s |
| `/comments` | 200 req/10s |
| `/tags` | 200 req/10s |
| `/public-search` | 350 req/10s |

### CLOB API

**General:**
| Endpoint | Rate Limit |
|----------|-----------|
| Overall limit | 9,000 req/10s |
| `/ok` | 100 req/10s |
| Balance allowance GET | 200 req/10s |
| Balance allowance POST | 50 req/10s |
| API key endpoints | 100 req/10s |

**Market Data:**
| Endpoint | Rate Limit |
|----------|-----------|
| `/book` | 1,500 req/10s |
| `/books` | 500 req/10s |
| `/price` | 1,500 req/10s |
| `/prices` | 500 req/10s |
| `/midpoint` | 1,500 req/10s |
| `/midpoints` | 500 req/10s |
| `/prices-history` | 1,000 req/10s |
| Tick size | 200 req/10s |

**Ledger:**
| Endpoint | Rate Limit |
|----------|-----------|
| `/trades`, `/orders`, `/order` | 900 req/10s |
| `/data/orders` | 500 req/10s |
| `/data/trades` | 500 req/10s |
| `/notifications` | 125 req/10s |

**Trading (Dual-Tier: Burst + Sustained):**
| Endpoint | Burst (10s) | Sustained (10 min) |
|----------|-------------|---------------------|
| `POST /order` | 3,500 | 36,000 |
| `DELETE /order` | 3,000 | 30,000 |
| `POST /orders` | 1,000 | 15,000 |
| `DELETE /orders` | 1,000 | 15,000 |
| `DELETE /cancel-all` | 250 | 6,000 |
| `DELETE /cancel-market-orders` | 1,000 | 1,500 |

### Data API

| Endpoint | Rate Limit |
|----------|-----------|
| General catchall | 1,000 req/10s |
| `/trades` | 200 req/10s |
| `/positions` | 150 req/10s |
| `/closed-positions` | 150 req/10s |
| `/ok` | 100 req/10s |

### Other
| Endpoint | Rate Limit |
|----------|-----------|
| Relayer `/submit` | 25 req/1 min |
| User PNL API | 200 req/10s |

---

## 9. Data Schemas <a name="data-schemas"></a>

### Event Object (Gamma API)

```json
{
  "id": "2890",
  "ticker": "nba-will-the-mavericks-beat-...",
  "slug": "nba-will-the-mavericks-beat-...",
  "title": "NBA: Will the Mavericks beat the Grizzlies by more than 5.5 points?",
  "description": "Resolution criteria text...",
  "resolutionSource": "https://www.nba.com/games",
  "startDate": "2021-12-04T00:00:00Z",
  "creationDate": "2021-12-04T00:00:00Z",
  "endDate": "2021-12-04T00:00:00Z",
  "image": "https://polymarket-upload.s3.us-east-2.amazonaws.com/...",
  "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/...",
  "active": true,
  "closed": true,
  "archived": false,
  "new": false,
  "featured": false,
  "restricted": false,
  "liquidity": 0,
  "volume": 1335.05,
  "openInterest": 0,
  "sortBy": "ascending",
  "category": "Sports",
  "published_at": "2022-07-27 14:40:02.064+00",
  "createdAt": "2022-07-27T14:40:02.074Z",
  "updatedAt": "2024-04-25T18:49:06.075795Z",
  "competitive": 0,
  "volume24hr": 0,
  "volume1wk": 0,
  "volume1mo": 0,
  "volume1yr": 0,
  "liquidityAmm": 0,
  "liquidityClob": 0,
  "commentCount": 8125,
  "markets": [ /* array of Market objects */ ],
  "series": [ /* series metadata */ ],
  "tags": [ /* tag objects */ ],
  "cyom": false,
  "closedTime": "2022-07-27T14:40:02.074Z",
  "showAllOutcomes": false,
  "showMarketImages": true,
  "enableNegRisk": false,
  "seriesSlug": "nba",
  "negRiskAugmented": false,
  "pendingDeployment": false,
  "deploying": false,
  "requiresTranslation": false
}
```

### Market Object (Gamma API)

```json
{
  "id": "12",
  "question": "Will Joe Biden get Coronavirus before the election?",
  "slug": "will-joe-biden-get-coronavirus",
  "category": "US-current-affairs",
  "description": "Resolution criteria...",
  "outcomes": ["Yes", "No"],
  "outcomePrices": ["0.95", "0.05"],
  "marketType": "normal",
  "active": false,
  "closed": true,
  "archived": false,
  "restricted": false,
  "approved": true,
  "ready": true,
  "funded": true,
  "volume": "32257.445115",
  "liquidity": "0.0",
  "bestBid": "0.95",
  "bestAsk": "0.96",
  "lastTradePrice": "0.95",
  "endDate": "2020-11-04T00:00:00Z",
  "createdAt": "2020-06-18T...",
  "updatedAt": "2024-04-25T...",
  "closedTime": "...",
  "conditionId": "0x...",
  "clobTokenIds": ["token_yes_id", "token_no_id"],
  "marketMakerAddress": "0x...",
  "fpmmLive": false,
  "volume24hr": 0,
  "volume1wk": 0,
  "volume1mo": 0,
  "volume1yr": 0,
  "events": [ /* parent event data */ ],
  "rfqEnabled": false,
  "feesEnabled": true,
  "enableOrderBook": true
}
```

Key relationships:
- **Event** contains one or more **Markets**
- Each **Market** has `clobTokenIds` array: index 0 = "Yes" token, index 1 = "No" token
- The `conditionId` is used for CLOB API market identification
- `clobTokenIds` (token_id / asset_id) is used for CLOB API price/orderbook queries
- Prices range from `0.00` to `1.00` (representing dollars / implied probability)

### Market Object (CLOB API)

```json
{
  "condition_id": "0x...",
  "question": "Will X happen?",
  "tokens": [
    {
      "token_id": "21742633...",
      "outcome": "Yes",
      "price": 0.52,
      "winner": false
    },
    {
      "token_id": "48331043...",
      "outcome": "No",
      "price": 0.48,
      "winner": false
    }
  ],
  "active": true,
  "closed": false,
  "accepting_orders": true,
  "fpmm": "",
  "rewards": { "rates": [], "min_size": 0, "max_spread": 0 },
  "maker_base_fee": 0,
  "taker_base_fee": 0,
  "minimum_order_size": "5",
  "minimum_tick_size": "0.01"
}
```

---

## 10. SDKs & Client Libraries <a name="sdks"></a>

### Python
```bash
pip install py-clob-client
```
GitHub: https://github.com/Polymarket/py-clob-client
Requires: Python 3.9+

### TypeScript
```bash
npm install @polymarket/clob-client ethers@5
```

### Key SDK Methods Summary

**Public (No Auth):**
| Method | Description |
|--------|-------------|
| `get_ok()` | Health check |
| `get_server_time()` | Server timestamp |
| `get_markets()` / `get_simplified_markets()` | Market lists |
| `get_market(condition_id)` | Single market |
| `get_order_book(token_id)` | Orderbook |
| `get_order_books([BookParams])` | Batch orderbooks |
| `get_price(token_id, side)` | Best price |
| `get_prices([BookParams])` | Batch prices |
| `get_midpoint(token_id)` | Mid price |
| `get_midpoints([BookParams])` | Batch midpoints |
| `get_spread(token_id)` | Spread |
| `get_spreads([BookParams])` | Batch spreads |
| `get_last_trade_price(token_id)` | Last trade |
| `get_last_trades_prices([BookParams])` | Batch last trades |
| `get_prices_history(params)` | Historical prices |
| `get_fee_rate_bps(token_id)` | Fee rate |
| `get_tick_size(token_id)` | Tick size |
| `get_neg_risk(token_id)` | Neg-risk check |
| `calculate_market_price(token_id, side, amount)` | Price estimate |
| `get_market_trades_events(condition_id)` | Recent trade events |

**L2 Auth (Trading):**
| Method | Description |
|--------|-------------|
| `create_and_post_order(OrderArgs)` | Place limit order |
| `create_and_post_market_order(MarketOrderArgs)` | Place market order |
| `post_order(signed_order)` | Submit pre-signed order |
| `post_orders([args])` | Batch submit (up to 15) |
| `cancel(order_id)` | Cancel one order |
| `cancel_orders([ids])` | Batch cancel (up to 3,000) |
| `cancel_all()` | Cancel all orders |
| `cancel_market_orders(params)` | Cancel by market |
| `get_order(order_id)` | Get order details |
| `get_open_orders(params)` | List open orders |
| `get_trades(params)` | Trade history |
| `get_trades_paginated(params)` | Paginated trades |
| `get_balance_allowance(params)` | Token balances |
| `update_balance_allowance(params)` | Refresh balance cache |
| `get_api_keys()` | List API keys |
| `delete_api_key()` | Revoke key |
| `get_notifications()` | Event log (48hr) |
| `drop_notifications(params)` | Dismiss notifications |

---

## Quick Reference: Common Data Flows

### Get all active markets with prices
```
1. GET https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100
   -> Returns events[] with nested markets[], each having clobTokenIds
2. For each market's clobTokenIds[0] (Yes token):
   GET https://clob.polymarket.com/midpoint?token_id={token_id}
   -> Returns midpoint price
```

### Stream real-time prices
```
1. Connect to wss://ws-subscriptions-clob.polymarket.com/ws/market
2. Send: { "assets_ids": ["token_id"], "type": "market", "custom_feature_enabled": true }
3. Receive: book snapshots, price_change events, last_trade_price events
4. Send PING every 10 seconds
```

### Get historical prices
```
GET https://clob.polymarket.com/prices-history?market={token_id}&interval=1d&fidelity=60
-> Returns { "history": [{ "t": timestamp, "p": price }, ...] }
```

### Look up a specific market by URL slug
```
GET https://gamma-api.polymarket.com/events/slug/will-trump-win-2024
-> Returns full event with all markets, token IDs, prices, metadata
```

---

## Token Allowances (For EOA/MetaMask Wallets)

Before trading with EOA wallets (signature type 0), approve tokens on these contracts:

**USDC.e:** `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
**Conditional Tokens:** `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`

Approve both on these three exchange contracts:
- `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
- `0xC5d563A36AE78145C45a50134d48A1215220f80a`
- `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`

---

## Documentation Links

- Main docs: https://docs.polymarket.com
- Full docs index: https://docs.polymarket.com/llms.txt
- Python SDK: https://github.com/Polymarket/py-clob-client
- TypeScript SDK: npm @polymarket/clob-client
