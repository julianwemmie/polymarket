# Real-Time News APIs for News-Reactive Prediction Market System

## Research Date: 2026-02-24

**Goal:** Detect news stories within minutes of publication and correlate them with
prediction market price movements (Polymarket, Kalshi, PredictIt).

---

## 1. NewsAPI.org

### Overview
Aggregates articles from 150,000+ news sources and blogs. REST API, no streaming.

### Endpoints
```
GET https://newsapi.org/v2/top-headlines       # Breaking news by country/category
GET https://newsapi.org/v2/everything           # Full-text search across all articles
GET https://newsapi.org/v2/top-headlines/sources # List available sources
```

### Pricing

| Tier       | Cost              | Requests         | Key Limitations                       |
|------------|-------------------|------------------|---------------------------------------|
| Developer  | Free              | 100/day          | 24-hour article delay, 1-month archive, localhost CORS only |
| Business   | $449/mo ($359/yr) | 250,000/mo       | Real-time access, 5-year archive      |
| Advanced   | $1,749/mo ($1,399/yr) | 2,000,000/mo | 99.95% SLA, priority support          |
| Enterprise | Custom            | Unlimited        | Enriched articles, clustering, on-prem |

### Data Format
JSON. Returns: `title`, `description`, `content` (truncated to 200 chars on free),
`url`, `urlToImage`, `publishedAt`, `source`.

### Latency
- **Free tier: 24-hour delay** -- completely unusable for real-time
- **Paid tier: Near real-time** (minutes, not seconds)

### Verdict for This Use Case
**BAD for free tier** (24h delay kills it). Business tier ($449/mo) is the minimum for
real-time. No streaming/webhooks -- you must poll. Decent for keyword-based searches
but not the fastest source. Better options exist for less money.

---

## 2. RSS Feeds (Direct Publisher Feeds)

### Best Political/Financial News RSS Feeds

#### Tier 1 -- Breaking News (Fastest)
```
# Reuters
https://www.reutersagency.com/feed/                     # Top News
https://www.reuters.com/arc/outboundfeeds/v3/all/rss.xml # All stories

# AP News
https://rsshub.app/apnews/topics/apf-topnews           # Top News (via RSSHub)

# BBC News
http://feeds.bbci.co.uk/news/rss.xml                    # Top Stories
http://feeds.bbci.co.uk/news/world/rss.xml              # World
http://feeds.bbci.co.uk/news/politics/rss.xml           # Politics
http://feeds.bbci.co.uk/news/business/rss.xml           # Business

# NPR
https://feeds.npr.org/1001/rss.xml                      # News
```

#### Tier 2 -- Financial/Markets
```
# CNBC
https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114  # Top News
https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664   # Finance
https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147   # Economy
https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135   # Politics

# Financial Times (limited free access)
https://www.ft.com/?format=rss

# Bloomberg (limited)
https://feeds.bloomberg.com/markets/news.rss
https://feeds.bloomberg.com/politics/news.rss
```

#### Tier 3 -- Political
```
# Politico
https://rss.politico.com/politics-news.xml
https://rss.politico.com/congress.xml

# The Hill
https://thehill.com/feed/

# RealClearPolitics
https://www.realclearpolitics.com/index.xml
```

### Efficient Polling Strategy
```python
# Optimal RSS polling approach:
# 1. Use HTTP conditional GET (ETag / If-Modified-Since headers)
# 2. Respect the <ttl> element in the feed (minutes between refreshes)
# 3. Typical polling intervals:
#    - Wire services (AP, Reuters): every 60 seconds
#    - Major outlets (BBC, CNBC):   every 2-3 minutes
#    - Others:                      every 5-10 minutes
#
# 4. Use feedparser (Python) or rss-parser (Node.js)
# 5. Deduplicate by GUID/link field
# 6. Track last-seen article timestamp per feed

import feedparser
import hashlib
from datetime import datetime

FEEDS = {
    "reuters": "https://www.reuters.com/arc/outboundfeeds/v3/all/rss.xml",
    "bbc_politics": "http://feeds.bbci.co.uk/news/politics/rss.xml",
    "cnbc_economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
}

seen = set()

def poll_feed(name, url):
    feed = feedparser.parse(url)
    new_articles = []
    for entry in feed.entries:
        uid = hashlib.md5(entry.link.encode()).hexdigest()
        if uid not in seen:
            seen.add(uid)
            new_articles.append({
                "source": name,
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", ""),
                "summary": entry.get("summary", ""),
            })
    return new_articles
```

### Latency
- Wire services: articles appear in RSS within **1-5 minutes** of publication
- Major outlets: **2-10 minutes**
- Smaller outlets: **5-30 minutes**

### Verdict for This Use Case
**GOOD -- and completely free.** RSS is the backbone of any budget news monitoring
system. Combine 10-20 carefully chosen feeds polling every 60-120 seconds and you
get excellent political/financial news coverage with sub-5-minute latency.
The main downside: no full-text search, no filtering -- you get everything and must
do your own NLP/keyword matching.

---

## 3. Twitter/X API

### Current State (as of Feb 2026)

Twitter/X gutted free API access in 2023. The API is now extremely expensive for
any meaningful use. A usage-based pricing model was announced in late 2025 but
remains in closed beta.

### Pricing Tiers

| Tier       | Cost          | Read Limit     | Key Features                           |
|------------|---------------|----------------|----------------------------------------|
| Free       | $0            | ~0 read access | Write-only (post tweets). Useless for monitoring. |
| Basic      | $200/mo       | 10,000 tweets/mo | 7-day search only. No filtered stream. |
| Pro        | $5,000/mo     | 1,000,000/mo   | Filtered stream, full-archive search   |
| Enterprise | ~$42,000+/mo  | Custom         | Full firehose, compliance streams      |

### Streaming
- **Filtered Stream** (rules-based real-time): Available at **Pro tier ($5,000/mo) and above**
  - Endpoint: `GET https://api.x.com/2/tweets/search/stream`
  - Rules endpoint: `POST https://api.x.com/2/tweets/search/stream/rules`
  - Supports up to 25 rules (Pro) or 1,000 rules (Enterprise)
  - True real-time: tweets arrive within **seconds** of posting
- **Basic tier ($200/mo)**: No streaming. Polling search only, 7-day window.
- **Free tier**: Cannot read tweets at all.

### Data Format
JSON. Tweet objects include: `id`, `text`, `created_at`, `author_id`, `entities`,
`public_metrics`, `context_annotations`.

### Latency
- Filtered stream: **seconds** (fastest possible for social media signals)
- Search polling: **30-60 seconds** depending on poll interval

### Verdict for This Use Case
**EXCELLENT signal quality but prohibitively expensive.** X is where news breaks first --
journalists, officials, and breaking news accounts post before articles are published.
For prediction markets, the "first signal" advantage of X is unmatched. But at $5K/mo
minimum for streaming, it is only viable if you are running a funded operation.

**Cheaper alternatives for X data:**
- **SocialData.tools** -- unofficial X data, ~$200-400/mo
- **Nitter instances** -- free but unreliable scraping
- **Apify Twitter scrapers** -- pay-per-result

---

## 4. Google News RSS

### How It Works
Google News aggregates articles from thousands of sources and exposes them via
undocumented RSS endpoints. Completely free, no API key needed.

### Endpoint Patterns

```bash
# Top headlines (by country/language)
https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en

# By topic (8 topics available)
https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en
# Topics: WORLD, NATION, BUSINESS, TECHNOLOGY, ENTERTAINMENT, SCIENCE, SPORTS, HEALTH

# By location
https://news.google.com/rss/headlines/section/geo/Washington%20DC?hl=en-US&gl=US&ceid=US:en

# By search query (MOST USEFUL)
https://news.google.com/rss/search?q=prediction+markets&hl=en-US&gl=US&ceid=US:en

# Time-filtered search (CRITICAL for real-time)
https://news.google.com/rss/search?q=Trump+tariff+when:1h&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q=Fed+rate+decision+when:4h&hl=en-US&gl=US&ceid=US:en

# Date-range search
https://news.google.com/rss/search?q=Ukraine+after:2026-02-23+before:2026-02-25&hl=en-US&gl=US&ceid=US:en
```

### Search Operators
| Operator      | Example                              | Function                    |
|---------------|--------------------------------------|-----------------------------|
| `OR`          | `SpaceX OR Boeing`                   | Match either term           |
| `""`          | `"interest rate"`                    | Exact phrase match          |
| `-`           | `Trump -golf`                        | Exclude term                |
| `intitle:`    | `intitle:tariff`                     | Term must be in headline    |
| `inurl:`      | `inurl:reuters`                      | Filter by source URL        |
| `when:Xh`     | `when:1h` (1-101 hours)             | Time recency filter         |
| `when:Xd`     | `when:7d`                            | Days recency filter         |
| `after:`      | `after:2026-02-20`                   | After specific date         |
| `before:`     | `before:2026-02-24`                  | Before specific date        |

### Key Tricks
1. **`when:1h` is your best friend** -- poll every 5 min with `when:1h` to catch fresh articles
2. **Google News URLs are obfuscated** -- article links go through `news.google.com/rss/articles/...`
   and must be decoded to get the actual article URL. Use the `google-news-decode` npm/pip package.
3. **Max 100 results per request**
4. **No official rate limit documented** but aggressive polling will get you temporarily blocked.
   Stay under 1 request per topic per 5 minutes.
5. **Combine topic feeds with search feeds** for maximum coverage

### Data Format
Standard RSS/XML (Atom). Fields: `<title>`, `<link>`, `<pubDate>`, `<description>`,
`<source url="...">`.

### Latency
- Articles typically appear **5-15 minutes** after publication on the original source
- Google's crawling/indexing adds delay vs. direct RSS from publishers

### Verdict for This Use Case
**VERY GOOD -- free and broad.** Best free option for keyword-monitored news. The `when:1h`
parameter makes it excellent for near-real-time monitoring. Slower than direct RSS feeds
from wire services, but covers far more sources. Use it as a second layer behind direct
RSS feeds.

---

## 5. Other Options

### 5a. GDELT Project (Global Database of Events, Language, and Tone)

**Endpoint:**
```
https://api.gdeltproject.org/api/v2/doc/doc?query=tariff&mode=artlist&format=json
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Cost          | **Completely free**, no API key required          |
| Rate limit    | Undocumented, but generous for reasonable use     |
| Update freq   | Every **15 minutes**                              |
| Archive       | Rolling 3-month window                            |
| Data format   | JSON, HTML, CSV                                   |
| Coverage      | 100+ languages, global, print + broadcast + web   |
| Features      | Sentiment, tone, themes, named entities, geo      |

**Latency:** 15-minute batches. Not true real-time, but excellent for enrichment.

**Verdict:** **GOOD as an enrichment layer.** Free, global, includes sentiment and entity
extraction. The 15-minute granularity is too slow to be your primary signal, but it is
excellent for context enrichment and historical correlation analysis.

---

### 5b. Alpaca News API (via Benzinga)

**WebSocket (streaming):**
```
wss://stream.data.alpaca.markets/v1beta1/news
```
**REST:**
```
GET https://data.alpaca.markets/v1beta1/news
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Cost          | **Free** with Alpaca account (paper trading OK)   |
| Auth          | API key + secret (header-based)                   |
| Streaming     | **Yes -- true WebSocket streaming**               |
| Data source   | Benzinga (professional financial news)            |
| Data format   | JSON over WebSocket                               |
| Fields        | headline, summary, content, symbols, author, timestamps |

**Subscribe to all news:**
```json
{"action": "subscribe", "news": ["*"]}
```

**Latency:** **Seconds.** True real-time streaming of financial news.

**Verdict:** **EXCELLENT for financial/market news -- and free.** This is arguably the best
free option for financial news streaming. Limited to market-relevant news (not political),
but for correlating news with market/prediction-market movements, it is outstanding.
Requires a free Alpaca brokerage account.

---

### 5c. Finnhub

**WebSocket:**
```
wss://ws.finnhub.io?token={API_KEY}
```
**REST:**
```
GET https://finnhub.io/api/v1/news?category=general&token={API_KEY}
GET https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2026-02-01&to=2026-02-24&token={API_KEY}
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Cost          | Free tier available (generous for testing)        |
| Rate limit    | 30 requests/second (free), higher on paid         |
| Streaming     | WebSocket for market data; news via REST polling  |
| Data format   | JSON                                              |
| Coverage      | Financial/market news, company-specific            |

**Verdict:** **DECENT for financial news.** Good REST API, generous free rate limits.
WebSocket is primarily for price data, not news. Use it to supplement Alpaca.

---

### 5d. NewsData.io

**Endpoint:**
```
GET https://newsdata.io/api/1/latest?apikey={KEY}&q=politics
GET https://newsdata.io/api/1/news?apikey={KEY}&q=economy&language=en
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Free tier     | 200 credits/day, 10 articles/credit               |
| Free delay    | **12-hour delay** on free tier                    |
| Paid (Basic)  | $200/mo -- real-time, 20K credits/mo              |
| Data format   | JSON                                              |
| Coverage      | Global, multi-language                             |

**Verdict:** **BAD for free tier** (12h delay). Paid tier is reasonable but Alpaca + Google
News RSS gives you more for free.

---

### 5e. NewsCatcher API

**Endpoint:**
```
GET https://v3-api.newscatcherapi.com/api/search?q=prediction+markets
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Free tier     | 21 calls/hour (15K/mo)                            |
| Paid          | Starting $399/mo                                  |
| Data format   | JSON with NLP enrichment                          |
| Coverage      | 120,000+ sources, 100+ countries                  |
| Features      | Sentiment, entity extraction, deduplication        |

**Verdict:** **GOOD if you need enriched data.** NLP features (sentiment, entities) are
valuable for prediction market correlation. Free tier is usable for prototyping.

---

### 5f. Polygon.io (now Massive)

**REST:**
```
GET https://api.polygon.io/v2/reference/news?ticker=AAPL&apiKey={KEY}
```

| Feature       | Detail                                           |
|---------------|--------------------------------------------------|
| Free tier     | Limited (5 API calls/min)                          |
| Paid          | $29/mo (Starter), $199/mo (Developer)              |
| Data format   | JSON                                              |
| Coverage      | US market news, ticker-linked                      |

**Verdict:** **OK for ticker-specific financial news.** Less useful for political news
that moves prediction markets.

---

## 6. Webhooks/Streaming Comparison

### Push-Based (Real-Time Streaming)

| Service              | Method     | Latency    | Cost               | Best For              |
|----------------------|------------|------------|--------------------|-----------------------|
| Alpaca News          | WebSocket  | Seconds    | Free               | Financial news        |
| X/Twitter Filtered   | SSE Stream | Seconds    | $5,000/mo          | Breaking news, political |
| Finnhub              | WebSocket  | Seconds    | Free (prices only) | Market data + news REST |
| FXStreet             | Webhooks   | Seconds    | Paid               | Forex news            |
| Contify              | Webhooks   | Minutes    | Enterprise         | Industry news         |

### Poll-Based (You Request Updates)

| Service              | Method     | Optimal Poll | Cost               | Best For              |
|----------------------|------------|-------------|--------------------|-----------------------|
| Google News RSS      | HTTP GET   | 5 min       | Free               | Broad keyword monitoring |
| Direct RSS feeds     | HTTP GET   | 1-2 min     | Free               | Wire service speed    |
| NewsAPI.org          | HTTP GET   | Varies      | $449+/mo for RT    | Keyword search        |
| GDELT                | HTTP GET   | 15 min      | Free               | Global events + NLP   |
| NewsCatcher          | HTTP GET   | Varies      | $399+/mo           | Enriched news data    |
| NewsData.io          | HTTP GET   | Varies      | $200+/mo for RT    | Global coverage       |

---

## Recommended Architecture for Prediction Market Correlation

### Budget: $0/month (Free Tier)

```
Layer 1 (Fastest): Alpaca News WebSocket
  - True streaming, seconds latency
  - Financial/market news only
  - Free with Alpaca account

Layer 2 (Fast): Direct RSS Feeds (10-20 feeds)
  - Reuters, AP, BBC, CNBC, Politico
  - Poll every 60-120 seconds
  - Covers political + financial

Layer 3 (Broad): Google News RSS
  - Keyword-specific searches with when:1h
  - Poll every 5 minutes per query
  - Catches stories from smaller outlets

Layer 4 (Enrichment): GDELT
  - Poll every 15 minutes
  - Adds sentiment, themes, entities
  - Historical correlation analysis
```

### Budget: ~$200-500/month

Add to the above:
- **NewsCatcher** ($399/mo) for NLP-enriched data with sentiment
- **OR** NewsAPI.org Business ($449/mo) for full-text search across 150K sources

### Budget: $5,000+/month

Add to the above:
- **X/Twitter Pro** ($5,000/mo) for filtered stream
  - This is where news truly breaks first
  - Monitor journalist accounts, official accounts, breaking news accounts
  - Seconds-level advantage over RSS

---

## Implementation Priority Order

1. **Alpaca WebSocket** -- set up first, free, streaming, immediate value
2. **RSS feed aggregator** -- 15-20 feeds, poll every 60s, free
3. **Google News RSS** -- keyword monitors with `when:1h`, free
4. **GDELT** -- enrichment layer, free
5. **NewsCatcher or NewsAPI.org** -- if budget allows, for NLP enrichment
6. **X/Twitter** -- if budget allows, for fastest-possible signal
