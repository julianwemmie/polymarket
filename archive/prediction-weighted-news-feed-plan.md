# Prediction-Weighted News Feed — Full Build Plan

## Core Concept

Two streams of data running in parallel:
1. **Every Polymarket price, updating in real-time** via WebSocket
2. **Every news story, arriving within minutes** via RSS + Alpaca streaming

The system watches both streams and asks: **"Which news stories actually moved prediction markets?"** Then ranks a feed by that signal.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  DATA INGESTION                  │
├────────────────────┬────────────────────────────┤
│  MARKET STREAM     │  NEWS STREAM               │
│                    │                             │
│  Polymarket WS     │  Alpaca WS (financial,     │
│  (all active       │    real-time, free)         │
│   token prices)    │  RSS polls every 60s        │
│                    │    (Reuters, AP, BBC, etc)  │
│                    │  Google News RSS every 5m   │
│                    │    (keyword searches)       │
└────────┬───────────┴──────────────┬─────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐    ┌───────────────────────┐
│  MOVEMENT       │    │  NEWS PROCESSOR       │
│  DETECTOR       │    │                       │
│                 │    │  Deduplicate           │
│  Rolling window │    │  Extract keywords      │
│  of prices per  │    │  Store with timestamp  │
│  market. Flag   │    │                       │
│  when Δ > 3%    │    │                       │
│  in 30 min      │    │                       │
└────────┬────────┘    └──────────┬────────────┘
         │                        │
         ▼                        ▼
┌─────────────────────────────────────────────────┐
│              ATTRIBUTION ENGINE                  │
│                                                  │
│  When a market moves:                            │
│  1. Pull all news from the last 60 min           │
│  2. Score each story's relevance to that market  │
│     (keyword overlap + LLM classification)       │
│  3. Attribute the move to the best-matching      │
│     story                                        │
│                                                  │
│  Result: news_story → [(market, Δprice, time)]   │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                FEED RANKER                       │
│                                                  │
│  impact_score = Σ |Δprice| × liquidity_weight    │
│  for all markets attributed to this story        │
│                                                  │
│  Rank stories by impact_score                    │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
                   [Frontend]
```

---

## Component 1: Market Stream

Connect to Polymarket's WebSocket and track prices for every active market.

### On startup:
1. Hit Gamma API `GET /events?active=true&closed=false&limit=100` to get all active events + their markets
2. Extract every `clobTokenIds[0]` (the YES token) from each market
3. Open a WebSocket to `wss://ws-subscriptions-clob.polymarket.com/ws/market`
4. Subscribe with all token IDs:
```json
{
  "assets_ids": ["token_id_1", "token_id_2", "..."],
  "type": "market"
}
```
5. Send `PING` every 10 seconds to keep alive
6. On each `price_change` or `last_trade_price` event, update in-memory price table

### Data structure:

```python
# Per market, keep a rolling window of prices
market_prices = {
    "token_abc": {
        "market_name": "Will TikTok be banned in 2026?",
        "event_slug": "tiktok-ban-2026",
        "tags": ["tech", "regulation"],
        "prices": deque([
            (1708800000, 0.35),
            (1708800060, 0.35),
            (1708800120, 0.36),
            # ... last 60 minutes
        ], maxlen=60)
    }
}
```

### Movement detection — every 30 seconds, scan all markets:
```python
for token_id, data in market_prices.items():
    if len(data["prices"]) < 2:
        continue
    current = data["prices"][-1][1]
    price_30m_ago = data["prices"][0][1]
    delta = abs(current - price_30m_ago)

    if delta >= 0.03:  # 3 percentage point move
        emit_movement_event(token_id, delta, direction, timestamp)
```

3% is a starting threshold. Tune per market type — big liquid markets (elections) might use 2%, small noisy markets might use 5%.

---

## Component 2: News Stream

Three sources running in parallel, all feeding into one deduplicated queue.

### Layer 1 — Alpaca WebSocket (real-time, seconds latency, free)
```python
# Connect to Alpaca's news stream
ws = websocket.connect("wss://stream.data.alpaca.markets/v1beta1/news")
ws.send(json.dumps({
    "action": "subscribe",
    "news": ["*"]  # all news
}))
# Each message gives you headline, summary, source, symbols, timestamp
```
Sign up for a free Alpaca paper trading account to get credentials. Covers financial/market news (Benzinga sourced).

### Layer 2 — Direct RSS feeds (1-minute polling, free)

Poll these every 60 seconds:
- `https://feeds.reuters.com/reuters/topNews`
- `https://feeds.reuters.com/reuters/politicsNews`
- `https://www.cnbc.com/id/100003114/device/rss/rss.html` (US politics)
- `https://www.cnbc.com/id/20910258/device/rss/rss.html` (economy)
- `https://feeds.bbci.co.uk/news/politics/rss.xml`
- `https://rss.politico.com/politics-news.xml`
- `https://thehill.com/feed/`

Use `feedparser` in Python. Track ETags to avoid re-fetching unchanged feeds.

### Layer 3 — Google News RSS (keyword searches, 5-minute polling, free)

For each active high-volume Polymarket category, run a keyword search:
```
https://news.google.com/rss/search?q=TikTok+ban+when:1h&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q=Trump+2028+election+when:1h&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q=Federal+Reserve+rate+when:1h&hl=en-US&gl=US&ceid=US:en
```

The `when:1h` parameter limits to the last hour. Poll every 5 minutes.

### Deduplication
Hash each story by `(normalized_headline, source_domain)`. Same story from multiple sources = keep earliest, merge sources.

### Unified news item schema:
```python
{
    "id": "sha256_hash",
    "headline": "Supreme Court agrees to hear TikTok case",
    "source": "reuters",
    "url": "https://...",
    "published_at": 1708800300,
    "ingested_at": 1708800320,
    "keywords": ["tiktok", "supreme court", "ban", "regulation"],
    "body_snippet": "The Supreme Court announced Tuesday..."
}
```

---

## Component 3: Attribution Engine

This is the brain. When a movement event fires, figure out **why**.

### Step 1 — Candidate retrieval

Pull all news stories from the last 60 minutes before the movement:
```python
candidates = news_store.query(
    time_range=(movement.timestamp - 3600, movement.timestamp)
)
```

### Step 2 — Fast keyword scoring

Before burning LLM tokens, do a cheap keyword overlap pass:
```python
market_keywords = extract_keywords(market.name + " " + " ".join(market.tags))
# e.g., {"tiktok", "ban", "2026", "regulation"}

for story in candidates:
    story.keyword_score = len(
        set(story.keywords) & set(market_keywords)
    ) / len(market_keywords)
```

Filter to stories with `keyword_score > 0.2`. This eliminates 90%+ of irrelevant stories cheaply.

### Step 3 — LLM attribution (for remaining candidates)

For the surviving stories (usually 0-5), ask Claude:

```
Given this prediction market:
  Name: "Will TikTok be banned in the US in 2026?"
  Current price moved from 0.35 to 0.42 (+7 points) in 20 minutes

And these recent news stories:
1. "Supreme Court agrees to hear TikTok ban case" (Reuters, 15 min ago)
2. "Tech stocks rally on earnings" (CNBC, 45 min ago)
3. "China warns of retaliation over trade restrictions" (BBC, 30 min ago)

Which story most likely caused this market movement?
Return JSON: {"story_index": 1, "confidence": 0.85, "reasoning": "..."}
```

Use Claude Haiku for this — cheap, fast, good at classification. ~10-50 calls per day.

### Step 4 — Store the attribution
```python
{
    "news_id": "abc123",
    "market_id": "token_xyz",
    "market_name": "Will TikTok be banned?",
    "price_delta": +0.07,
    "confidence": 0.85,
    "timestamp": 1708800600
}
```

---

## Component 4: Impact Scoring & Feed Ranking

Each news story accumulates a score:

```python
story.impact_score = sum(
    abs(attribution.price_delta) * market_liquidity_weight
    for attribution in story.attributions
)
```

Weight by liquidity because a 10-point move on a $5M volume election market is way more meaningful than a 10-point move on a $500 volume meme market. Pull 24h volume from Gamma API as the weight.

### Feed output:
```python
[
    {
        "headline": "Supreme Court agrees to hear TikTok case",
        "source": "reuters",
        "url": "...",
        "impact_score": 0.42,
        "markets_moved": [
            {"name": "TikTok banned 2026?", "delta": "+7%", "volume_24h": "$2.1M"},
            {"name": "TikTok available in US?", "delta": "-5%", "volume_24h": "$800K"}
        ],
        "published_at": "2026-02-24T14:30:00Z"
    },
    ...
]
```

Stories with no market impact don't appear. Stories that moved multiple markets rank highest.

---

## Component 5: Frontend

Clean feed UI — Next.js for production, Streamlit for quick prototype.

### Each card shows:
- Headline + source + timestamp
- Impact score as a colored bar (green for high impact)
- List of markets affected with sparklines showing the price jump
- Link to the original article
- Link to each Polymarket market

### Filters:
- Category (politics, crypto, tech, sports)
- Time range (last hour, today, this week)
- Minimum impact score

### Bonus views:
- **"Nothing Burgers"** — stories with huge media coverage (high Google News volume) but zero market impact
- **"Source Leaderboard"** — which publications move markets most? Reuters vs CNN vs Twitter?
- **"Speed Score"** — how long after a story breaks does the market fully incorporate it?

---

## Tech Stack

| Component | Tool | Why |
|-----------|------|-----|
| Market WebSocket | Python `websockets` | Native async, handles Polymarket's ping/pong |
| News polling | Python `aiohttp` + `feedparser` | Async HTTP for parallel RSS polling |
| Alpaca stream | Python `websockets` | Same pattern as market stream |
| Attribution LLM | Claude Haiku API | Cheap, fast, good at classification |
| Storage | SQLite or DuckDB | Simple, no infra, fast enough for this volume |
| Frontend | Next.js or Streamlit | Streamlit for prototype, Next for production |
| Orchestration | Single Python process with `asyncio` | Everything runs in one event loop |

---

## Build Order (what to build first)

1. **Market price tracker** — connect to WS, store prices, detect movements. Get this working and printing movement events to console.
2. **RSS news ingestor** — poll 5-10 feeds, deduplicate, store. Print new stories to console.
3. **Wire them together** — when a movement fires, look up recent news, do keyword matching. Print "Market X moved because of Story Y" to console.
4. **Add LLM attribution** — replace keyword-only matching with Claude Haiku for the final scoring step.
5. **Build the feed UI** — serve the ranked stories through a web interface.
6. **Add Alpaca streaming + Google News** — more news sources for better coverage.
7. **Add the fun stuff** — nothing burger detector, source leaderboard, speed analysis.

Steps 1-3 could be done in a day. Steps 4-5 in another day. Working prototype in 2-3 days.

---

## API Reference Files

Detailed API research is saved alongside this plan:
- `polymarket-api-research.md` — full Polymarket API docs (CLOB, Gamma, Data, WebSocket)
- `news-api-research.md` — full news API research (Alpaca, RSS feeds, Google News, etc.)
