# PRD: "Who Can't Keep a Secret?" — Polymarket Insider Trading Detector

## What I'm Building

An interactive web app that detects and surfaces insider trading on Polymarket, then aggregates findings to answer: **which companies and governments leak the most?**

The tool analyzes on-chain trading data from the Polygon blockchain to flag suspicious wallets — accounts that placed large, concentrated bets shortly before major events resolved, often from newly created wallets with no other trading history. It then aggregates these findings by entity (company, government, organization) to produce an **institutional leak scoreboard**.

### Core Features

1. **Suspicion Engine** — For every resolved Polymarket market, analyze all trades and score wallets on insider signals:
   - Wallet age (created days/hours before the event)
   - Bet concentration (only bets on 1-2 related markets)
   - Timing (large positions taken shortly before resolution)
   - Profit (outsized returns relative to position history)
   - Coordination (multiple wallets sharing funding sources or trading in sync)

2. **Entity Leaderboard** — Aggregate suspicious activity by the company or institution the market is *about*:
   - "OpenAI markets had 12 suspicious wallets across 8 markets"
   - "U.S. military/foreign policy markets had the highest insider signal"
   - Ranked, scored, with drill-down into the evidence

3. **Wallet Detective View** — Click into any flagged wallet and see:
   - Full trade timeline overlaid against news/event timestamps
   - Fund flow tracing (where did the money come from?)
   - Linked wallets (shared funding sources)
   - Profit/loss breakdown

4. **Market Investigation View** — Click into any market and see:
   - Timeline of all trades, with suspicious ones highlighted
   - Distribution of wallet ages for traders
   - Volume spikes correlated with (or preceding) news events

### Data Sources
- **Polymarket API** (public, no auth) — market metadata, trade history, wallet positions, profiles
- **Polygon blockchain** (via PolygonScan / RPC) — fund flow tracing, wallet creation dates, proxy wallet ownership
- **Dune Analytics** — pre-indexed on-chain data for efficient querying
- **Jon Becker's dataset** (33GB, 2021-2025) — historical baseline for backtesting detection algorithms
- **News APIs** — for correlating trade timing against public announcements

## What New Technology I'm Implementing

This project pushes me into three domains I've never worked in:

1. **Blockchain data analysis** — I've never queried on-chain data, traced wallet funding flows, or worked with smart contract interactions. Understanding Polygon's transaction model, ERC-1155 conditional tokens, and proxy wallet architecture is entirely new.

2. **Anomaly/fraud detection algorithms** — Building scoring heuristics for insider trading detection, including temporal analysis, behavioral clustering, and network graph analysis of wallet relationships.

3. **Working with prediction market data** — Understanding how prediction markets work mechanically (order books, conditional tokens, position splits/merges) and building tooling on top of their APIs.

## How This Pushes Beyond What I Think Is Possible

The ambitious part isn't any single feature — it's the full pipeline:

- Ingesting and processing blockchain transaction data at scale
- Building detection heuristics that actually surface real suspicious activity (not just noise)
- Aggregating wallet-level findings up to institutional-level insights (the "leak scoreboard")
- Presenting it all in a way that's compelling enough to go viral

The stretch that excites me most: **actually finding something new.** The known cases (Maduro, OpenAI, Google) have been reported. If this tool surfaces a pattern or a suspicious entity that nobody has publicly called out yet — that's the demo moment. "I built this tool, and here's what it found."

### Stretch Goals
- Wallet clustering via shared funding source analysis
- Real-time monitoring of new trades as they happen (WebSocket)
- LLM-powered narrative generation ("tell me the story of this wallet")
- Cross-referencing with news APIs to automatically identify the gap between "bet placed" and "news published"
