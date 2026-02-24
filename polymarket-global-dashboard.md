# Global Polymarket Dashboard — "State of the World"

A Bloomberg terminal for reality. Polymarket is quietly building the most honest probability map of the future. This dashboard makes that visible.

---

## 1. The World Map View

An interactive globe/map where markets are pinned geographically.

- Markets sized by volume
- Colored by directional momentum (shifting toward YES = green, NO = red)
- Click any market to drill into detail
- Geographic clustering for regional risk assessment

---

## 2. Category Dashboards

Each one like a mission control screen:

### Geopolitics
- Conflicts (ceasefire odds, escalation probabilities)
- Diplomacy (treaties, deals, sanctions)
- Elections worldwide
- Composite "Geopolitical Risk Index" chart over time

### Economy & Markets
- Fed decisions, recession odds, inflation targets
- Crypto milestones (BTC/ETH price targets, ETF approvals)
- Trade war / tariff outcomes

### Tech & AI
- AGI timelines, model release dates
- Company IPOs, acquisitions, valuations
- Regulation outcomes (AI Act, antitrust)
- Product launch predictions

### Science & Health
- Pandemic risk (bird flu, COVID variants)
- Drug approvals, clinical trial outcomes
- Climate milestones

### Culture & Sports
- Awards (Oscars, Grammys)
- Championships
- Viral cultural moments

### US Politics
- Approval ratings via markets
- Legislation odds
- 2028 cycle tracking

---

## 3. The Momentum Board

What's moving RIGHT NOW — the most dynamic piece:

- **Biggest Movers (24h)** — markets with largest probability shifts
- **Top Volume** — where the money is flowing
- **New Markets** — just launched, sortable by category
- **Volume Spikes** — anomaly detection for unusual activity surges

---

## 4. Composite Indices

Build synthetic indices from clusters of related markets:

| Index | Composition | What it captures |
|-------|-------------|------------------|
| **Global Stability Index** | Weighted avg of conflict, diplomacy, trade war markets | Overall geopolitical temperature |
| **AI Acceleration Index** | AGI timelines + regulation + funding + product launches | Pace of AI progress |
| **US Economic Confidence** | Recession + Fed + unemployment + GDP markets | Economic outlook |
| **Pandemic Risk Index** | Bird flu + COVID variant + WHO declaration markets | Health threat level |
| **Crypto Sentiment Index** | BTC/ETH price targets + ETF + regulation markets | Crypto market conviction |

Each index has a historical chart — watch "global stability" rise and fall over months.

**Nobody else is building this.**

---

## 5. News ↔ Markets Correlation Engine

The killer feature. Real-time feed showing:

- Breaking news headline
- Which markets moved in response, by how much, with volume
- Aggregate insights: "News about Fed policy moves markets 3.2x faster than geopolitical news this month"
- Reverse lookup: "What news caused this market to spike?"
- Lag analysis: how quickly do markets price in news?

---

## 6. Historical Resolution Database

Searchable archive answering: "How accurate were prediction markets?"

- Calibration charts across all resolved markets
- Category accuracy breakdowns (markets are better at elections than tech timelines)
- "Markets were wrong" hall of fame — biggest upsets
- Time-to-accuracy analysis — how far in advance do markets converge on truth?
- Brier scores by category and timeframe

---

## Key Differentiators

| Existing tools | This dashboard |
|---|---|
| Show individual markets | Shows the **narrative** across markets |
| Static odds | **Momentum, velocity, acceleration** of odds |
| No context | **News-correlated** probability shifts |
| Raw data | **Composite indices** that tell a story |
| Trader-focused | **Anyone** can read the state of the world |

## The Pitch

> "The most honest dashboard on the internet — because people are betting real money on it."

---

## Suggested Build Order

1. **Momentum Board** — most immediately useful, API-friendly
2. **Category Dashboards** — structure and organization layer
3. **Composite Indices** — most novel, hardest to find elsewhere
4. **News ↔ Markets Engine** — killer feature, needs NLP pipeline
5. **World Map View** — impressive visually, good for launch
6. **Historical Resolution Database** — long-term value, needs data accumulation

## Tech Stack Considerations

- React + D3/Visx for charts and visualizations
- WebSockets for real-time updates
- Polymarket CLOB API + Polygon RPC for market data
- NLP pipeline (Claude API) for news sentiment analysis
- MapboxGL or Deck.gl for the globe/map view
- Time-series DB (TimescaleDB or InfluxDB) for historical data
