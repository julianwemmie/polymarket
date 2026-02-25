# Data Pipeline Feasibility: Scanning All of Polymarket

## Summary

We investigated whether it's feasible to pull complete historical trade data across all of Polymarket for insider trading detection. The Polymarket REST API has hard caps that make full historical backfill impossible. Two alternative methods exist — see [historical-data-methods.md](./2026-02-25-historical-data-methods.md).

## Polymarket Scale (measured)

| Metric | Count |
|---|---|
| Total resolved markets | ~466,648 |
| Active markets | ~30,000–31,000 |
| Total markets | ~496,000 |
| Global trade rate (current) | ~71 trades/sec (~6.1M/day) |
| Unique wallets per 1,000 trades | ~370 |

## Polymarket API Limitations (discovered)

The Polymarket REST APIs (Gamma, Data, CLOB) are designed for the website, not bulk historical research.

**Data API (`data-api.polymarket.com`):**
- `/trades` per market: returns only the **last ~1,000 trades** (the $1.5B 2024 election market returned ~1,000 out of millions)
- `/trades` global stream: hard cap at **offset 3,000** (~42 seconds of history)
- `/activity` per wallet: caps at ~4,000–5,000 records
- `/holders` per market: only current holders (winners on resolved markets — losers zeroed out and disappear)
- No "list all wallets" endpoint

**CLOB API (`clob.polymarket.com`):**
- `/prices-history`: max ~14 day window per request, returns ~165 data points/week at 1h fidelity
- Only works for tokens with recent activity; old markets return empty

**Gamma API (`gamma-api.polymarket.com`):**
- Market metadata is fully accessible — all 496K markets paginate correctly
- Every market has `clobTokenIds` (even 2020-era markets)

**Bottom line:** Gamma API is complete for metadata. Data API is a real-time/recent-only API, not a historical archive.

## Rate Limits (from Polymarket docs)

| API | Endpoint | Limit |
|---|---|---|
| Gamma | `/markets` | 300 req/10s |
| Gamma | `/events` | 500 req/10s |
| CLOB | `/prices-history` | 1,000 req/10s |
| Data | `/trades` | 200 req/10s |
| Data | `/positions` | 150 req/10s |
| Data | General | 1,000 req/10s |

Rate limits are enforced via Cloudflare throttling (requests delayed/queued, not rejected). Per-IP.

## Solution

See [2026-02-25-historical-data-methods.md](./2026-02-25-historical-data-methods.md) for the two methods of getting complete historical data (Goldsky subgraph scraping and Polygon blockchain scanning).

## Detection Signals (fleshed out)

### Signal 2: Timing Relative to Information Release

**Approach A (self-contained, no external news API):** Use price spikes as a proxy for information release. A jump from 20% to 80% in 30 minutes = information entered the market.

- Detect spikes from CLOB price history or from the trade data itself
- Look backward: which wallets entered positions 30min–4hrs before the spike
- Track which wallets repeatedly appear in pre-spike windows across markets
- For ongoing monitoring: WebSocket (`wss://ws-live-data.polymarket.com`) streams prices in real-time across all markets — watch for spikes and trigger investigation immediately

**Approach B (richer signal, more complexity):** Correlate with actual news timestamps from GDELT, NewsAPI, or Twitter/X. Requires NLP/LLM layer to match markets to news events. Layer on later.

### Signal 3: Statistical Implausibility

With complete per-wallet trade history, compute:
- **Win rate on low-probability bets:** Buying YES at <20% that resolves YES. Even great forecasters hit ~30-40%. Hitting 80%+ across 10+ such bets is astronomically unlikely.
- **Brier score vs market consensus:** Wallet positions consistently outperform market implied probabilities by a wide margin → p-value against null model.
- **Kelly criterion violation:** Insiders put 50%+ of capital into single niche bets and win. Skilled traders diversify.
- **Concentration in niche markets:** High accuracy on obscure markets where no one should have high confidence.

## Key APIs Reference

| API | Base URL | Auth | Use |
|---|---|---|---|
| Gamma | `gamma-api.polymarket.com` | None | Market metadata, outcomes, token IDs |
| Data | `data-api.polymarket.com` | None | Recent trades, holders, wallet activity |
| CLOB | `clob.polymarket.com` | None (read) | Price history, order books |
| Polygon RPC | Provider-dependent | API key | **Complete historical trade data** |
| WebSocket | `wss://ws-live-data.polymarket.com` | None | Real-time price stream |

