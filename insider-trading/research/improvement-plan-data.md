# Data Pipeline Improvement Plan

> Based on audit of current codebase and Polymarket API research.
> Date: 2026-02-24

---

## Current State Summary

What we have today:

| Component | Status | Limitations |
|-----------|--------|-------------|
| Event/market ingestion | Working | Gamma `/events` only fetches 100 events per run, sorted by volume; no active market support |
| Trade ingestion | Working | Hard-capped at 200 trades per market via Data API `/trades`; misses most trade history |
| PolygonScan integration | Built but disabled | `api_key="demo"` in config; `_get_or_create_wallet` skips blockchain queries entirely |
| Suspicion scoring | Working | 4 factors (wallet age, bet concentration, timing, profit) but wallet age always scores 0.5 (unknown) since blockchain is disabled |
| Data freshness | Manual only | `POST /api/ingest?limit=15` triggered from frontend; no scheduling, no real-time feed |
| Price history | Not captured | No price snapshots; cannot detect pre-resolution price spikes |
| Order book data | Not captured | No spread/depth data; cannot detect liquidity manipulation |
| Position/holder data | Not captured | Cannot see who holds large positions in a market |
| Cross-market correlation | Not possible | No way to link wallets across markets outside of what we ingest in a single run |

---

## Tier 1: Quick Wins -- Data We Are Leaving on the Table

### 1.1 Increase Trade Fetch Limit from 200 to 10,000

**What:** The Data API `/trades` endpoint supports `limit` up to 10,000 and `offset` up to 10,000. We currently fetch only 200 trades per market. For high-volume markets (millions in volume), 200 trades is a tiny fraction.

**Why it matters:** With 200 trades, we are scoring wallets based on an incomplete and biased sample (most recent trades only). We miss early movers who traded well before resolution -- exactly the pattern insider trading detection should catch.

**Source:** `GET https://data-api.polymarket.com/trades?market={conditionId}&limit=10000&offset=0`
- Max `limit`: 10,000
- Max `offset`: 10,000
- For markets with >10k trades: make two calls (`offset=0,limit=10000` then `offset=10000,limit=10000`) for up to 20,000 trades

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- In `get_trades()`, change `batch_size = min(limit, 100)` to `batch_size = min(limit, 10000)` (the API supports it; no reason to page at 100)
- Change the default `limit` parameter from 500 to 10000

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- Line 176: Change `limit=200` to `limit=10000` in the `get_trades()` call

**Rate limits:** `/trades` is 200 req/10s. Even with two calls per market (20k trades), this is well within limits for a batch run of 15-50 markets.

**Complexity:** Small. Two constant changes.

---

### 1.2 Enable PolygonScan Wallet Analysis

**What:** The `BlockchainClient` class in `blockchain.py` is fully implemented but never called. The `_get_or_create_wallet` function in `ingest.py` has a comment saying "Skip blockchain queries for now." Meanwhile, the suspicion engine's wallet age factor (25% of the total score) always returns 0.5 because `wallet.first_seen` is always `None`.

**Why it matters:** Wallet age is one of the strongest insider trading signals. A wallet created days before placing a large winning bet is far more suspicious than a wallet with years of history. With blockchain disabled, every wallet gets the same "unknown" score, effectively wasting 25% of our scoring capacity.

**Source:** PolygonScan API -- already configured in `config.py` and `blockchain.py`:
- `GET https://api.polygonscan.com/api?module=account&action=txlist&address={addr}&startblock=0&endblock=99999999&page=1&offset=1&sort=asc`
- `GET https://api.polygonscan.com/api?module=account&action=txlist&address={addr}&startblock=0&endblock=99999999&page=1&offset=10&sort=asc` (for funding source)

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- In `_get_or_create_wallet`, replace the skip block (lines 108-118) with actual calls:
```python
creation_date = await blockchain_client.get_wallet_creation_date(addr)
funding_source = await blockchain_client.get_funding_source(addr)
wallet = Wallet(
    address=addr,
    first_seen=creation_date,
    market_count=0,
    total_volume=0.0,
    total_profit=0.0,
    suspicion_score=0.0,
    funding_source=funding_source,
)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/config.py`
- The `polygonscan_api_key` default of `"demo"` needs to be replaced with a real key via `.env`. Free API keys are available at https://polygonscan.com/myapikey.

**Rate limits:** PolygonScan free tier: 5 req/s. Two calls per unique wallet (creation date + funding source). The `_RATE_LIMIT_DELAY = 0.22` in `blockchain.py` already handles this. For a market with 200 unique wallets, this adds ~90 seconds per market. Consider batching or caching aggressively.

**Complexity:** Small. Uncomment existing logic, get a free API key.

---

### 1.3 Fetch Holder/Position Data for Each Market

**What:** The Data API has a `/holders` endpoint that returns the top holders of each outcome token for a market, including wallet address, position size, and outcome. We do not use this at all.

**Why it matters:** Knowing who holds the largest positions adds a "whale concentration" signal. If a single wallet holds a disproportionate share of the winning outcome, and that position was built shortly before resolution, that is a strong insider indicator. This also lets us detect wallets that are large holders but do not appear in our trade sample (e.g., they traded earlier than our 10k trade window).

**Source:** `GET https://data-api.polymarket.com/holders?market={conditionId}&limit=100`
- Response: Array of `{ proxyWallet, pseudonym, asset, amount, outcomeIndex, name, bio, profileImage, displayUsernamePublic }`
- No authentication required

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Add new method to `PolymarketClient`:
```python
async def get_holders(self, condition_id: str, limit: int = 100) -> list[dict[str, Any]]:
    params = {"market": condition_id, "limit": limit}
    resp = await self._client.get("https://data-api.polymarket.com/holders", params=params)
    resp.raise_for_status()
    return resp.json()
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add a new `MarketHolder` model (or extend `Trade` with a `position_size` field):
```python
class MarketHolder(Base):
    __tablename__ = "market_holders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    pseudonym: Mapped[str | None] = mapped_column(String, nullable=True)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- After fetching trades, call `get_holders()` and save results
- Feed holder concentration data into the suspicion engine

**Rate limits:** General Data API is 1,000 req/10s. One call per market. Negligible.

**Complexity:** Small-medium. New model, new API call, minor scoring integration.

---

### 1.4 Store Additional Market Fields (Liquidity, Open Interest, clobTokenIds)

**What:** The Gamma API returns `liquidity`, `liquidityNum`, `openInterest`, `clobTokenIds`, `volume24hr`, and `endDate` per market. Our `map_market()` method discards all of these.

**Why it matters:**
- `openInterest`: Total collateral at stake -- needed to normalize position sizes (is a $50k position big or small in context?)
- `liquidityNum`: Low liquidity markets are easier to manipulate and more susceptible to insider activity
- `clobTokenIds`: Required to subscribe to the CLOB WebSocket for real-time trades (needed for Tier 3) and to call CLOB pricing endpoints
- `volume24hr`: Enables spike detection relative to recent activity
- `endDate`: Needed to distinguish "market about to close" from "market just resolved"

**Source:** Already returned by `GET https://gamma-api.polymarket.com/events` -- no new API calls needed.

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add columns to `Market`:
```python
liquidity: Mapped[float] = mapped_column(Float, default=0.0)
open_interest: Mapped[float] = mapped_column(Float, default=0.0)
volume_24hr: Mapped[float] = mapped_column(Float, default=0.0)
clob_token_ids: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON array
end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```
- Update `MarketResponse` and `MarketDetailResponse` Pydantic schemas

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- In `map_market()`, extract and return the new fields:
```python
"liquidity": float(raw.get("liquidityNum", 0) or 0),
"open_interest": float(raw.get("openInterest", 0) or 0),
"volume_24hr": float(raw.get("volume24hr", 0) or 0),
"clob_token_ids": json.dumps(raw.get("clobTokenIds", [])),
"end_date": _parse_iso(raw.get("endDate")),
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- Pass new fields when constructing the `Market` ORM instance

**Rate limits:** No additional API calls.

**Complexity:** Small. Schema additions, field mapping.

---

### 1.5 Also Ingest Active (Unresolved) Markets

**What:** Currently we only fetch `closed=True` events. We never look at active markets.

**Why it matters:** Insider trading detection is most valuable *before* a market resolves. If we only look at closed markets, we can only do post-mortem analysis. Ingesting active markets lets us flag suspicious patterns in real time (or near-real-time) so action can be taken.

**Source:** Same Gamma endpoint: `GET https://gamma-api.polymarket.com/events?active=true&closed=false&order=volume&limit=50`

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Modify `get_events()` to accept an `active` parameter:
```python
async def get_events(self, limit=50, offset=0, closed=True, active=None):
    params = {"limit": limit, "offset": offset, "order": "volume", "ascending": "false"}
    if active is not None:
        params["active"] = str(active).lower()
    if closed is not None:
        params["closed"] = str(closed).lower()
    ...
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- Add a separate pass or parameter for active market ingestion
- Modify the profit calculation to handle unresolved markets (profit=None for active markets, scoring must account for this)

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/suspicion.py`
- `_score_profit` needs a code path for `profit=None` (active market), e.g., skip profit scoring and re-weight the other factors

**Rate limits:** One extra Gamma API call per ingest run. Trivial.

**Complexity:** Medium. Need to handle the "no resolution yet" case across scoring, profit calculation, and the UI.

---

## Tier 2: New Data Sources That Unlock New Detection Capabilities

### 2.1 CLOB Price History for Pre-Resolution Spike Detection

**What:** The CLOB API provides historical price data per token at configurable granularity. We can fetch minute-by-minute or hourly price history for any market's outcome tokens.

**Why it matters:** A sudden price spike (e.g., "Yes" price jumping from 0.30 to 0.85 over a few hours before resolution) is one of the most visible signs of insider activity. Without price history, we cannot detect this pattern at all. Our current scoring only looks at individual trade timing and profit -- it misses the market-level price movement context.

**Source:** `GET https://clob.polymarket.com/prices-history`
- Parameters: `market` (token_id from `clobTokenIds`), `startTs`, `endTs`, `interval` (`1h`, `6h`, `1d`), `fidelity` (minutes)
- Response: `{ "history": [{ "t": 1700000000, "p": 0.65 }, ...] }`
- No authentication required

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Add new method:
```python
async def get_price_history(
    self,
    token_id: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    interval: str = "1h",
    fidelity: int = 60,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"market": token_id, "interval": interval, "fidelity": fidelity}
    if start_ts:
        params["startTs"] = start_ts
    if end_ts:
        params["endTs"] = end_ts
    resp = await self._client.get(f"{self.clob_url}/prices-history", params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("history", [])
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add `PriceSnapshot` model:
```python
class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), nullable=False)
    token_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/suspicion.py`
- Add new scoring factor `_score_price_spike`:
  - Compute max price delta in the 24-48h window before resolution
  - Score based on magnitude: >0.40 delta = 1.0, >0.25 = 0.7, >0.10 = 0.3, else 0.0
  - Cross-reference: did this wallet's largest trade happen *before* the spike began?

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- After saving trades, fetch price history for each token in the market
- Requires the `clobTokenIds` field (from improvement 1.4)
- Pass price history to the suspicion engine

**Rate limits:** CLOB general rate limit is 9,000 req/10s. Two calls per market (one per outcome token). Negligible.

**Complexity:** Medium. New model, new API integration, new scoring factor, weight rebalancing.

---

### 2.2 Order Book Snapshots (Spread and Depth)

**What:** The CLOB provides real-time order book data -- bids, asks, spread, and depth per token.

**Why it matters:** Insiders may manipulate order book depth to move prices. Detecting thin books (low liquidity at key price levels) at the time of large trades indicates potential manipulation. A large buy against a thin order book is more suspicious than the same buy against deep liquidity.

**Source:**
- `GET https://clob.polymarket.com/book?token_id={token_id}` -- Full order book with bids[], asks[], spread
- `GET https://clob.polymarket.com/spread?token_id={token_id}` -- Current bid-ask spread
- `GET https://clob.polymarket.com/midpoint?token_id={token_id}` -- Midpoint price
- All are public, no auth required

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Add methods:
```python
async def get_order_book(self, token_id: str) -> dict[str, Any]:
    resp = await self._client.get(f"{self.clob_url}/book", params={"token_id": token_id})
    resp.raise_for_status()
    return resp.json()

async def get_spread(self, token_id: str) -> dict[str, Any]:
    resp = await self._client.get(f"{self.clob_url}/spread", params={"token_id": token_id})
    resp.raise_for_status()
    return resp.json()
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add `OrderBookSnapshot` model:
```python
class OrderBookSnapshot(Base):
    __tablename__ = "order_book_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), nullable=False)
    token_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    best_bid: Mapped[float] = mapped_column(Float, nullable=False)
    best_ask: Mapped[float] = mapped_column(Float, nullable=False)
    spread: Mapped[float] = mapped_column(Float, nullable=False)
    bid_depth_10pct: Mapped[float] = mapped_column(Float, nullable=True)  # total $ within 10% of best bid
    ask_depth_10pct: Mapped[float] = mapped_column(Float, nullable=True)
```

**Use in scoring:** Compare trade size against order book depth at time of trade. A $50,000 buy against $10,000 of ask depth signals potential market impact intent.

**Rate limits:** `/books` endpoint: 500-1,500 req/10s. For batch analysis of active markets this is fine. For real-time monitoring (Tier 3), snapshots should be taken periodically (e.g., every 5 minutes) rather than per-trade.

**Complexity:** Medium. New model, new methods, periodic snapshot logic, scoring integration.

---

### 2.3 Wallet Activity Across Markets via Data API `/activity`

**What:** The Data API `/activity` endpoint returns all on-chain activity for a wallet: trades, splits, merges, redemptions, conversions. It supports time-range filtering and activity type filtering.

**Why it matters:** Our current bet concentration scoring only counts distinct market IDs from our own trade table. If we have only ingested 15 markets, a wallet that traded across 500 markets still looks like it traded in 1-2 markets. The `/activity` endpoint gives us a wallet's full trading footprint across all of Polymarket.

Additionally, `SPLIT` and `MERGE` events are invisible to our current pipeline. A wallet that splits positions (creating outcome tokens from USDC) before a large price move is a different behavioral pattern from one that buys on the open market.

**Source:** `GET https://data-api.polymarket.com/activity?user={proxyWallet}&limit=500`
- Parameters: `user` (required), `limit` (max 500), `offset`, `type` (CSV: `TRADE,SPLIT,MERGE,REDEEM,REWARD,CONVERSION`), `start`, `end` (unix timestamps), `sortBy`, `sortDirection`
- Response fields: `proxyWallet`, `timestamp`, `conditionId`, `type`, `size`, `usdcSize`, `transactionHash`, `price`, `asset`, `side`, `outcomeIndex`, etc.

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Add method:
```python
async def get_wallet_activity(
    self,
    wallet_address: str,
    limit: int = 500,
    activity_types: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"user": wallet_address, "limit": limit}
    if activity_types:
        params["type"] = activity_types
    resp = await self._client.get("https://data-api.polymarket.com/activity", params=params)
    resp.raise_for_status()
    return resp.json()
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add `WalletActivity` model to capture non-trade activity:
```python
class WalletActivity(Base):
    __tablename__ = "wallet_activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    market_id: Mapped[str | None] = mapped_column(String, nullable=True)
    activity_type: Mapped[str] = mapped_column(String, nullable=False)  # TRADE, SPLIT, MERGE, REDEEM, etc.
    size: Mapped[float] = mapped_column(Float, nullable=False)
    usdc_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    transaction_hash: Mapped[str | None] = mapped_column(String, nullable=True)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/suspicion.py`
- Improve `_score_bet_concentration` to use real cross-market count from `/activity` data
- Add new factor `_score_activity_pattern`: flag wallets that predominantly use splits/merges (more sophisticated/unusual behavior for retail users)

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/ingest.py`
- For each wallet flagged above the threshold (score >= 0.3), fetch their full activity
- Update `wallet.market_count` with the true count from the activity data

**Rate limits:** General Data API: 1,000 req/10s. The risk is on the wallet side -- if a market has 500 unique wallets, fetching activity for all of them is 500 calls. Mitigation: only fetch activity for wallets that already score above a lower threshold (e.g., 0.2) in the initial pass.

**Complexity:** Medium. New model, new API call, conditional deep-dive logic, scoring updates.

---

### 2.4 Wallet Positions and PnL via Data API `/positions`

**What:** The `/positions` endpoint returns a wallet's current open positions across all markets, including average price, initial value, current value, realized P&L, and percent P&L.

**Why it matters:** This gives a portfolio-level view of a wallet. A wallet with 95% win rate across many markets is far more suspicious than one that occasionally gets lucky. This also reveals if a wallet has *current* positions in active markets that we should be monitoring.

**Source:** `GET https://data-api.polymarket.com/positions?user={proxyWallet}&limit=500`
- Response fields per position: `proxyWallet`, `asset`, `conditionId`, `size`, `avgPrice`, `initialValue`, `currentValue`, `cashPnl`, `percentPnl`, `totalBought`, `realizedPnl`, `percentRealizedPnl`, `curPrice`, `redeemable`, `title`, `slug`, `endDate`

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/polymarket.py`
- Add method:
```python
async def get_wallet_positions(self, wallet_address: str, limit: int = 500) -> list[dict[str, Any]]:
    params = {"user": wallet_address, "limit": limit}
    resp = await self._client.get("https://data-api.polymarket.com/positions", params=params)
    resp.raise_for_status()
    return resp.json()
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Extend `Wallet` with portfolio-level fields:
```python
win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
total_positions: Mapped[int] = mapped_column(Integer, default=0)
total_realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/suspicion.py`
- Add `_score_win_rate` factor:
  - Compute win rate from position data (positions where `realizedPnl > 0` / total closed positions)
  - Score: >90% win rate over 10+ markets = 1.0; >80% = 0.7; >70% = 0.3; else 0.0
  - This is a strong signal: consistent winners across many markets suggest information advantage

**Rate limits:** `/positions`: 150 req/10s. Apply the same selective-fetch strategy: only pull positions for wallets that pass initial suspicion threshold.

**Complexity:** Medium. New API call, model extension, new scoring factor.

---

### 2.5 Goldsky Subgraph for Complete Trade History

**What:** The Polymarket Orders subgraph on Goldsky provides every `OrderFilled` event on-chain, with maker and taker addresses, amounts, and timestamps. Unlike the Data API's 20,000-trade ceiling, subgraph queries can paginate through arbitrarily large datasets.

**Why it matters:** For high-volume markets (election markets can have 100k+ trades), the Data API ceiling of 20,000 trades means we miss 80%+ of the trade history. The subgraph gives us complete coverage. It also gives us maker/taker pairs -- crucial for detecting coordinated trading (wash trading, self-dealing) which the Data API does not expose.

**Source:** GraphQL POST to `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn`

Example query:
```graphql
{
  orderFilledEvents(
    first: 1000,
    skip: 0,
    orderBy: timestamp,
    orderDirection: desc,
    where: { market: "0x..." }
  ) {
    id
    maker
    taker
    makerAmountFilled
    takerAmountFilled
    makerAssetId
    takerAssetId
    timestamp
  }
}
```

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/subgraph.py` (new file)
```python
class SubgraphClient:
    ORDERS_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"

    async def get_order_fills(self, market: str, first: int = 1000, skip: int = 0) -> list[dict]:
        query = """
        query($market: String!, $first: Int!, $skip: Int!) {
          orderFilledEvents(first: $first, skip: $skip, orderBy: timestamp, orderDirection: desc, where: {market: $market}) {
            id maker taker makerAmountFilled takerAmountFilled makerAssetId takerAssetId timestamp
          }
        }
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.ORDERS_URL, json={"query": query, "variables": {"market": market, "first": first, "skip": skip}})
            resp.raise_for_status()
            return resp.json()["data"]["orderFilledEvents"]
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- The existing `Trade` model can be extended with:
```python
maker_address: Mapped[str | None] = mapped_column(String, nullable=True)
taker_address: Mapped[str | None] = mapped_column(String, nullable=True)
data_source: Mapped[str] = mapped_column(String, default="data_api")  # "data_api" | "subgraph"
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/config.py`
- Add: `goldsky_orders_url: str = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"`

**New detection capability:** Maker/taker address pairs enable wash trading detection. If the same entity is both maker and taker (through different proxy wallets linked by funding source), that is coordinated manipulation.

**Rate limits:** Goldsky public endpoints are generally generous (not explicitly documented). The Graph Gateway alternative requires an API key and has 100k queries/month free tier. Use Goldsky for now, fall back to The Graph if throttled.

**Complexity:** Medium-large. New service file, GraphQL client, data reconciliation between Data API and subgraph results, new detection logic.

---

### 2.6 PnL Subgraph for Historical Profit Data

**What:** The Polymarket PnL subgraph tracks per-user, per-market profit and loss over time. This is pre-computed on-chain data that avoids us having to estimate profits from trade data.

**Why it matters:** Our current profit calculation in `ingest.py` (lines 222-243) estimates profit from individual trades and resolution outcomes. This is approximate -- it does not account for partial fills, multiple entry/exit points, or DCA strategies. The PnL subgraph gives us the authoritative profit figure.

**Source:** GraphQL POST to `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn`

Example query:
```graphql
{
  userMarketPnls(
    first: 100,
    where: { market: "0x...", pnl_gt: "1000" }
    orderBy: pnl,
    orderDirection: desc
  ) {
    user
    market
    pnl
    volume
    numTrades
  }
}
```

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/subgraph.py`
- Add PnL query method to `SubgraphClient`:
```python
PNL_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn"

async def get_market_pnl(self, market: str, min_pnl: float = 0, first: int = 100) -> list[dict]:
    ...
```

**Integration:** Replace estimated profit with authoritative PnL data when available. Use as primary input to `_score_profit` in the suspicion engine.

**Rate limits:** Same as 2.5 (Goldsky public endpoints).

**Complexity:** Small-medium. Additional query method in existing subgraph client, data mapping.

---

## Tier 3: Infrastructure Changes for Real-Time Monitoring and Scale

### 3.1 WebSocket Market Channel for Live Trade Streaming

**What:** Subscribe to the Polymarket Market WebSocket channel to receive real-time trade events (`last_trade_price` messages) for every monitored market. No polling. No auth required.

**Why it matters:** This is the single biggest architectural upgrade. Currently we only see trades after the fact (batch ingestion). With the WebSocket feed, we can detect suspicious patterns as they happen: sudden volume spikes, large single trades, rapid price movements. This enables alerting.

**Source:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

Subscription message:
```json
{
  "assets_ids": ["<token_id_1>", "<token_id_2>", ...],
  "type": "market",
  "custom_feature_enabled": true
}
```

Key message types received:
- `last_trade_price` -- fires on every trade: `{ asset_id, market, price, size, side, fee_rate_bps, timestamp }`
- `price_change` -- fires on order book changes: `{ market, price_changes[], timestamp }`
- `book` -- full order book snapshots after trades

Keepalive: send `PING` every 10 seconds; server responds `PONG`.

Dynamic subscription: add/remove tokens without reconnecting:
```json
{ "assets_ids": ["<new_token>"], "operation": "subscribe" }
```

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/websocket.py` (new file)
```python
import asyncio
import json
import logging
import websockets

logger = logging.getLogger(__name__)

class MarketWebSocket:
    URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    def __init__(self, on_trade, on_price_change):
        self.on_trade = on_trade          # callback(trade_data)
        self.on_price_change = on_price_change
        self._ws = None
        self._subscribed_tokens: set[str] = set()

    async def connect(self):
        self._ws = await websockets.connect(self.URL)
        asyncio.create_task(self._keepalive())
        asyncio.create_task(self._listen())

    async def subscribe(self, token_ids: list[str]):
        msg = {
            "assets_ids": token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await self._ws.send(json.dumps(msg))
        self._subscribed_tokens.update(token_ids)

    async def _keepalive(self):
        while self._ws:
            await self._ws.send("PING")
            await asyncio.sleep(10)

    async def _listen(self):
        async for message in self._ws:
            if message == "PONG":
                continue
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type == "last_trade_price":
                await self.on_trade(data)
            elif msg_type == "price_change":
                await self.on_price_change(data)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/alert.py` (new file)
- Real-time analysis: buffer trades per market, detect volume spikes (e.g., 10x normal rate), large single trades (> $X), rapid price movements (> Y% in Z minutes)
- Generate alerts stored in a new `Alert` model

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add `Alert` model:
```python
class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String, nullable=False)  # "volume_spike", "large_trade", "price_spike"
    severity: Mapped[str] = mapped_column(String, nullable=False)    # "low", "medium", "high", "critical"
    details: Mapped[str] = mapped_column(String, nullable=False)     # JSON
    wallet_address: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/main.py`
- Start WebSocket connection on app lifespan startup
- Subscribe to token IDs of all active monitored markets
- Add SSE endpoint for streaming alerts to the frontend

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/config.py`
- Add: `ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"`

**Dependencies:** Requires improvement 1.4 (storing `clobTokenIds`) and 1.5 (ingesting active markets).

**Rate limits:** WebSocket connections are not rate-limited in the traditional sense. The keepalive requirement (PING every 10s) is the main constraint. One connection handles all subscribed tokens.

**Complexity:** Large. New async service, connection management, reconnection logic, real-time analysis pipeline, alert model, frontend integration.

---

### 3.2 Scheduled Ingestion with APScheduler or Celery Beat

**What:** Replace the manual `POST /api/ingest` trigger with scheduled periodic ingestion. Run at configurable intervals (e.g., every 30 minutes for active markets, every 6 hours for full historical re-scan).

**Why it matters:** Manual ingestion means the database is only as fresh as the last time someone clicked the button. For a monitoring tool, this is unacceptable. Even without the WebSocket (Tier 3.1), scheduled ingestion gives near-real-time coverage.

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/tasks/scheduler.py` (new file)
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.tasks.ingest import ingest_markets

scheduler = AsyncIOScheduler()

# Active markets: frequent scan
scheduler.add_job(ingest_markets, "interval", minutes=30, kwargs={"limit": 50}, id="active_scan")

# Historical backfill: less frequent
scheduler.add_job(ingest_markets, "interval", hours=6, kwargs={"limit": 200}, id="historical_scan")
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/main.py`
- Import and start the scheduler in the lifespan handler:
```python
from src.tasks.scheduler import scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.start()
    yield
    scheduler.shutdown()
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/config.py`
- Add: `ingest_interval_minutes: int = 30`, `historical_scan_hours: int = 6`

**New dependency:** `apscheduler>=3.10` (or `celery` + `redis` for a more robust distributed setup).

**Complexity:** Small-medium. Straightforward scheduler setup, but need to handle concurrent runs (locking) and error recovery.

---

### 3.3 Database Migration from SQLite to PostgreSQL

**What:** Replace SQLite with PostgreSQL for concurrent write support, full-text search, and better performance at scale.

**Why it matters:** SQLite does not handle concurrent writes well. With scheduled ingestion (3.2) and a WebSocket trade stream (3.1) both writing to the database simultaneously, SQLite will be a bottleneck. PostgreSQL also enables `INSERT ... ON CONFLICT` for efficient upserts, `JSONB` columns for flexible metadata, and materialized views for the leaderboard aggregation.

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/config.py`
- Change default: `database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/insider_trading"`

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/database.py`
- No changes needed if using SQLAlchemy async -- the engine handles the dialect switch
- Add Alembic for schema migrations:
  - `alembic init alembic`
  - Configure `alembic/env.py` to use `async_engine` and `Base.metadata`
  - Generate initial migration: `alembic revision --autogenerate -m "initial"`

**New dependencies:** `asyncpg`, `alembic`

**Complexity:** Medium. Engine swap is simple, but requires provisioning a PostgreSQL instance, migrating existing data, and setting up Alembic for future schema changes.

---

### 3.4 Funding Source Graph (Wallet Clustering)

**What:** Build a graph of wallet relationships using funding source data from PolygonScan. When wallet A funds wallet B, they are linked. Transitively, wallets funded by the same source are clustered together.

**Why it matters:** Insiders typically use multiple wallets to spread their trades and avoid detection. If five wallets all funded from the same source each place $10k bets on the same outcome, that is a $50k coordinated position -- far more suspicious than any individual $10k bet. Without clustering, each wallet is scored independently and may fly under the radar.

**Source:**
- PolygonScan (already built): `get_funding_source(address)` returns the first funder
- Data API: `/activity` with `type=SPLIT,MERGE` reveals wallets that split/merge with each other
- Subgraph: `orderFilledEvents` with maker/taker pairs

**Code changes:**

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/models.py`
- Add `WalletCluster` model:
```python
class WalletCluster(Base):
    __tablename__ = "wallet_clusters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_label: Mapped[str] = mapped_column(String, nullable=False)
    wallet_address: Mapped[str] = mapped_column(String, ForeignKey("wallets.address"), nullable=False)
    link_type: Mapped[str] = mapped_column(String, nullable=False)  # "funding", "maker_taker", "shared_funder"
    linked_to: Mapped[str] = mapped_column(String, nullable=False)  # the other wallet in the relationship
```

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/clustering.py` (new file)
- Union-Find / graph-based clustering algorithm
- Input: wallet addresses + funding sources from the Wallet table
- Output: clusters of related wallets
- Score boost: if a cluster's aggregate position in a single market exceeds a threshold, all wallets in the cluster get a scoring bonus

File: `/Users/julia/Documents/code/fractal-tech/Week 4/brainstorming/insider-trading/backend/src/services/suspicion.py`
- Add `_score_cluster_activity` factor:
  - If wallet is part of a cluster with >2 wallets all trading the same market same direction: score 0.8-1.0
  - If wallet is isolated: score 0.0

**Rate limits:** PolygonScan calls are the bottleneck (5 req/s). Funding source lookup is one call per wallet. Cluster computation itself is purely in-memory.

**Complexity:** Large. Graph algorithms, new model, cross-wallet correlation, significant scoring changes.

---

## Summary Table

| ID | Improvement | Tier | New Data | Key Files Changed | Complexity |
|----|------------|------|----------|-------------------|------------|
| 1.1 | Raise trade limit to 10k | 1 | More trades per market | `polymarket.py`, `ingest.py` | Small |
| 1.2 | Enable PolygonScan | 1 | Wallet age, funding source | `ingest.py`, `.env` | Small |
| 1.3 | Fetch holder data | 1 | Top holders per market | `polymarket.py`, `models.py`, `ingest.py` | Small-Med |
| 1.4 | Store extra market fields | 1 | Liquidity, OI, token IDs | `models.py`, `polymarket.py`, `ingest.py` | Small |
| 1.5 | Ingest active markets | 1 | Pre-resolution data | `polymarket.py`, `ingest.py`, `suspicion.py` | Medium |
| 2.1 | CLOB price history | 2 | Price time series | `polymarket.py`, `models.py`, `suspicion.py`, `ingest.py` | Medium |
| 2.2 | Order book snapshots | 2 | Spread, depth | `polymarket.py`, `models.py` | Medium |
| 2.3 | Wallet activity `/activity` | 2 | Splits, merges, full history | `polymarket.py`, `models.py`, `suspicion.py`, `ingest.py` | Medium |
| 2.4 | Wallet positions/PnL | 2 | Win rate, portfolio view | `polymarket.py`, `models.py`, `suspicion.py` | Medium |
| 2.5 | Goldsky Orders subgraph | 2 | Complete trades, maker/taker | `subgraph.py` (new), `models.py`, `config.py` | Med-Large |
| 2.6 | PnL subgraph | 2 | Authoritative profit data | `subgraph.py`, `suspicion.py` | Small-Med |
| 3.1 | WebSocket live trades | 3 | Real-time trade stream | `websocket.py` (new), `alert.py` (new), `models.py`, `main.py` | Large |
| 3.2 | Scheduled ingestion | 3 | Periodic freshness | `scheduler.py` (new), `main.py`, `config.py` | Small-Med |
| 3.3 | PostgreSQL migration | 3 | Concurrency, performance | `config.py`, `database.py`, Alembic setup | Medium |
| 3.4 | Wallet clustering | 3 | Coordinated trading detection | `clustering.py` (new), `models.py`, `suspicion.py` | Large |

---

## Recommended Implementation Order

**Phase 1 (Week 1):** 1.1, 1.2, 1.4 -- Immediate data quality improvements with minimal code changes. Get a PolygonScan API key first.

**Phase 2 (Week 2):** 1.3, 1.5, 2.1 -- Add holder data, active market monitoring, and price history. This enables pre-resolution spike detection for the first time.

**Phase 3 (Weeks 3-4):** 2.3, 2.4, 2.6, 3.2 -- Deep wallet analysis, portfolio-level scoring, and scheduled ingestion. The app becomes useful without manual intervention.

**Phase 4 (Weeks 5-8):** 2.5, 3.1, 3.3, 3.4 -- Full subgraph integration, real-time WebSocket monitoring, database upgrade, and wallet clustering. This is the "production-grade" phase.
