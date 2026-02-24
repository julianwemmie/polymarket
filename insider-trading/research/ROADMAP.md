# Insider Trading Detector — Implementation Roadmap

> Unified plan combining detection algorithm and data pipeline improvements.
> Optimized for maximum parallelism.

---

## Phase 1: Fix What's Broken + Grab Free Data (Days 1-2)

Everything here is independent — all can be done in parallel.

### Track A: Data Pipeline Fixes
| Task | What | Files | Est |
|------|------|-------|-----|
| **A1** Raise trade limit 200 → 10,000 | Change two constants. We're throwing away 98% of available trades. | `polymarket.py`, `ingest.py` | 15 min |
| **A2** Store extra Gamma fields | `map_market()` discards `clobTokenIds`, `liquidityNum`, `openInterest`, `volume24hr`, `endDate` — all needed later | `models.py`, `polymarket.py`, `ingest.py` | 1-2 hrs |
| **A3** Get PolygonScan API key + enable blockchain queries | `BlockchainClient` is fully built but skipped. Uncomment in `_get_or_create_wallet()` | `ingest.py`, `.env` | 1 hr |

### Track B: Scoring Engine Fixes
| Task | What | Files | Est |
|------|------|-------|-----|
| **B1** Fix wallet age scoring | Derive `first_seen` from earliest `Trade.timestamp` instead of relying on disabled PolygonScan. Activates dead 25% of score weight | `ingest.py` | 30 min |
| **B2** Market-relative profit scoring | Replace absolute dollar thresholds ($100/$1k/$5k/$10k) with profit-as-%-of-market-volume | `suspicion.py`, `ingest.py` | 1 hr |
| **B3** Add contrarian signal | New factor: buying low-probability outcomes that win. Every documented insider case shows this. Highest-value single signal | `suspicion.py`, `ingest.py` | 2-3 hrs |
| **B4** Add position size factor | New factor: trade size relative to market volume | `suspicion.py`, `ingest.py` | 1-2 hrs |

### Parallelism
```
Track A (data):  A1 ──┐
                 A2 ──┤  all independent, do simultaneously
                 A3 ──┘

Track B (algo):  B1 ──┐
                 B2 ──┤  all independent, do simultaneously
                 B3 ──┤
                 B4 ──┘

A and B tracks are fully independent of each other.
```

### Exit Criteria
- Ingesting 10,000 trades per market instead of 200
- All 6 scoring factors active (age, concentration, timing, profit, contrarian, position size)
- Rebalanced weights: timing 20%, contrarian 25%, profit 15%, age 15%, position 15%, concentration 10%
- `clobTokenIds` stored per market (needed for Phase 3)
- Wallet `first_seen` and `funding_source` populated from either trades or PolygonScan

---

## Phase 2: New Detection Dimensions (Days 3-6)

Depends on Phase 1 being complete (scoring engine and data pipeline stabilized).

### Track C: Cross-Wallet Detection (new capability)
| Task | What | Files | Est |
|------|------|-------|-----|
| **C1** Temporal cluster detection | Find groups of wallets placing same-direction bets within a 2hr window. Catches coordinated insider rings (Maduro pattern) | NEW `temporal_cluster.py`, `ingest.py` | 4-6 hrs |
| **C2** Funding source clustering | Group wallets sharing a funder (hub-and-spoke). Uses `Wallet.funding_source` from Phase 1 A3 | NEW `wallet_cluster.py`, `ingest.py` | 4-6 hrs |

### Track D: Smarter Scoring
| Task | What | Files | Est |
|------|------|-------|-----|
| **D1** Win rate anomaly | Track per-wallet win/loss across markets. 95%+ win rate = statistical impossibility | `models.py` (schema), `suspicion.py`, `ingest.py` | 4-6 hrs |
| **D2** Market-level statistical baselines | Compute per-market trade size distributions, use z-scores instead of hard thresholds | `suspicion.py`, `ingest.py` | 3-4 hrs |
| **D3** Multi-trade timing analysis | Replace single-largest-trade timing with volume-weighted timing across all trades + escalation detection | `suspicion.py` | 2-3 hrs |

### Track E: New Data Sources
| Task | What | Files | Est |
|------|------|-------|-----|
| **E1** Fetch holder data per market | `/holders` endpoint — who holds largest positions in winning outcome? | `polymarket.py`, NEW model, `ingest.py` | 3-4 hrs |
| **E2** CLOB price history | `/prices-history` — detect pre-resolution price spikes | `polymarket.py`, NEW `PriceSnapshot` model, `suspicion.py` | 4-6 hrs |
| **E3** Wallet activity endpoint | `/activity` — get full cross-market footprint, splits, merges | `polymarket.py`, NEW model, `suspicion.py` | 4-6 hrs |

### Track F: Calibration
| Task | What | Files | Est |
|------|------|-------|-----|
| **F1** Backtest framework | Score known insider wallets (Maduro, AlphaRaccoon, ricosuave666) to calibrate threshold and weights | NEW `backtest.py` | 4-6 hrs |

### Parallelism
```
Track C:  C1 ──┐  independent of each other
          C2 ──┘  (C2 benefits from A3 data but doesn't block on it)

Track D:  D1 ──┐
          D2 ──┤  all independent
          D3 ──┘

Track E:  E1 ──┐
          E2 ──┤  all independent (E2 needs A2's clobTokenIds)
          E3 ──┘

Track F:  F1 ──── should run AFTER C+D are done (tests the new scoring)

All of C, D, E can run in parallel with each other.
F1 runs last in this phase as validation.
```

### Exit Criteria
- Temporal clusters detected and scored (coordinated wallet groups flagged)
- Funding source clusters identified
- Win rate tracked per wallet, anomalous win rates flagged
- z-score based anomaly detection replacing static thresholds
- Price history stored, pre-resolution spikes detectable
- Backtest shows known insiders scoring above threshold with low false positive rate

---

## Phase 3: Go Live — Real-Time + Active Markets (Days 7-12)

This is the architectural shift from forensic tool to live monitoring platform.

### Track G: Infrastructure
| Task | What | Files | Est |
|------|------|-------|-----|
| **G1** Ingest active (unresolved) markets | Fetch `closed=false` events. Handle scoring without resolution data | `polymarket.py`, `ingest.py`, `suspicion.py` | 4-6 hrs |
| **G2** Scheduled ingestion | APScheduler: active markets every 30min, historical every 6hrs. Replace manual button | NEW `scheduler.py`, `main.py`, `config.py` | 3-4 hrs |
| **G3** PostgreSQL migration | SQLite can't handle concurrent writes from scheduler + websocket. Add Alembic migrations | `config.py`, `database.py`, Alembic setup | 4-6 hrs |

### Track H: Real-Time Pipeline
| Task | What | Files | Est |
|------|------|-------|-----|
| **H1** WebSocket trade stream | Subscribe to `wss://ws-subscriptions-clob.polymarket.com/ws/market` for live `last_trade_price` events | NEW `websocket.py` | 6-8 hrs |
| **H2** Real-time alert engine | Buffer trades, detect volume spikes, large trades, rapid price moves. Generate alerts | NEW `alert.py`, NEW `Alert` model | 6-8 hrs |
| **H3** Alert API + SSE endpoint | Stream alerts to frontend in real-time | NEW router, `main.py` | 3-4 hrs |

### Track I: Subgraph Integration
| Task | What | Files | Est |
|------|------|-------|-----|
| **I1** Goldsky Orders subgraph | Complete trade history with maker/taker pairs. Enables wash trading detection | NEW `subgraph.py` | 4-6 hrs |
| **I2** PnL subgraph | Authoritative profit figures replacing our estimates | `subgraph.py`, `suspicion.py` | 2-3 hrs |

### Parallelism
```
Track G:  G1 ──→ G2 ──→ G3    (sequential: need active markets before scheduling,
                                need scheduling before postgres becomes critical)

Track H:  H1 ──→ H2 ──→ H3    (sequential: websocket feeds alert engine feeds API)

Track I:  I1 ──┐  independent of each other
          I2 ──┘

G, H, and I are independent tracks — can run in parallel.
G3 should be done before H1 goes to production (concurrent writes).
```

### Exit Criteria
- Active markets being monitored alongside closed ones
- Ingestion runs automatically on schedule
- WebSocket streaming trades in real-time for monitored markets
- Alerts generated for volume spikes, large trades, price movements
- Frontend shows live alert feed
- PostgreSQL handling concurrent writes from scheduler + websocket
- Complete trade data available via subgraph for high-volume markets

---

## Phase 4: Advanced Intelligence (Days 13-20)

The differentiators — things no existing open-source tool does.

### Track J: Graph Intelligence
| Task | What | Files | Est |
|------|------|-------|-----|
| **J1** Full wallet graph with networkx | Louvain community detection across funding, temporal, and behavioral edges | NEW `graph_analysis.py`, `models.py` (WalletEdge) | 2-3 days |
| **J2** Sybil ring scoring | Cluster-level suspicion: if a cluster's aggregate position in a market is large + profitable, boost all member scores | `suspicion.py`, `ingest.py` | 1 day |

### Track K: External Signal Correlation
| Task | What | Files | Est |
|------|------|-------|-----|
| **K1** News event correlation | Match trade timestamps against news publication times via NewsAPI. Detect pre-news trading | NEW `news_correlator.py`, `models.py`, `config.py` | 2-3 days |
| **K2** Social media signal layer | Twitter/X API for mentions of market topics. Trades preceding public discussion = suspicious | Extension of `news_correlator.py` | 1-2 days |

### Track L: Statistical Learning
| Task | What | Files | Est |
|------|------|-------|-----|
| **L1** Historical baseline model | numpy/scipy anomaly detection trained on 500+ markets. Replace hand-tuned thresholds | NEW `baseline.py`, NEW `train_baseline.py` | 2-3 days |
| **L2** Manual labeling UI | Let analysts mark flags as confirmed/false-positive. Builds labeled dataset | Frontend + new API endpoints | 1-2 days |

### Track M: Investigation UX
| Task | What | Files | Est |
|------|------|-------|-----|
| **M1** LLM investigation summaries | Synthesize all signals into analyst-readable briefs via Claude API | NEW `llm_summarizer.py`, new API endpoint | 1-2 days |
| **M2** Network graph visualization | Force-directed wallet cluster visualization in frontend | Frontend (d3/cytoscape) | 2-3 days |
| **M3** Market timeline view | Combined timeline: trades, price curve, news events, suspicious flags overlaid | Frontend component | 2-3 days |
| **M4** Export/reporting | Generate PDF investigation reports for a wallet or market | Backend + frontend | 1-2 days |

### Parallelism
```
Track J:  J1 ──→ J2    (sequential: need graph before scoring)
Track K:  K1 ──→ K2    (sequential: news infra before social)
Track L:  L1 ──┐       independent
          L2 ──┘
Track M:  M1 ──┐
          M2 ──┤       all independent
          M3 ──┤
          M4 ──┘

J, K, L, M are all independent tracks — maximum parallelism.
```

---

## Dependency Graph (Full)

```
PHASE 1 (all parallel)
├─ A1 (trade limit)
├─ A2 (extra fields) ─────────────────────────────→ E2 (price history needs clobTokenIds)
├─ A3 (polygonscan) ──────────────────────────────→ C2 (funding clusters need funding_source)
├─ B1 (wallet age fix)
├─ B2 (relative profit)
├─ B3 (contrarian signal)
└─ B4 (position size)

PHASE 2 (parallel tracks, after Phase 1)
├─ C1 (temporal clusters)
├─ C2 (funding clusters) ────────────────────────→ J1 (full graph uses funding data)
├─ D1 (win rate)
├─ D2 (market baselines) ────────────────────────→ L1 (statistical model extends baselines)
├─ D3 (multi-trade timing)
├─ E1 (holders)
├─ E2 (price history) ───────────────────────────→ H2 (alert engine uses price spikes)
├─ E3 (wallet activity)
└─ F1 (backtest) ── runs after C+D complete

PHASE 3 (parallel tracks, after Phase 2)
├─ G1 (active markets) ──→ G2 (scheduler) ──→ G3 (postgres)
├─ H1 (websocket) ──→ H2 (alerts) ──→ H3 (alert API)
├─ I1 (orders subgraph)
└─ I2 (pnl subgraph)

PHASE 4 (parallel tracks, after Phase 3)
├─ J1 (wallet graph) ──→ J2 (sybil scoring)
├─ K1 (news) ──→ K2 (social)
├─ L1 (baseline model)
├─ L2 (labeling UI)
├─ M1 (LLM summaries)
├─ M2 (network graph viz)
├─ M3 (timeline view)
└─ M4 (export/reporting)
```

---

## New Dependencies by Phase

| Phase | Package | Why |
|-------|---------|-----|
| 1 | (none) | All changes use existing deps |
| 2 | (none) | Pure Python, existing httpx |
| 3 | `websockets`, `apscheduler`, `asyncpg`, `alembic` | WebSocket client, scheduling, PostgreSQL |
| 4 | `networkx`, `numpy`, `scipy`, `anthropic` | Graph analysis, stats, LLM API |

## API Keys Needed

| Phase | Key | Cost | Where to get |
|-------|-----|------|-------------|
| 1 | PolygonScan | Free tier (5 req/s) | polygonscan.com/myapikey |
| 4 | NewsAPI | Free tier (100 req/day) | newsapi.org |
| 4 | Anthropic | Pay per use | console.anthropic.com |
| 4 | Twitter/X API | Free tier limited | developer.x.com |

---

## What Each Phase Unlocks

**After Phase 1:** The scoring engine actually works. All factors active, market-relative scoring, contrarian detection. Catches obvious cases like Maduro (large bets on low-odds outcomes right before resolution).

**After Phase 2:** Cross-wallet detection works. Catches coordinated insider rings, statistically anomalous win rates, pre-resolution price spikes. The backtest validates against known cases.

**After Phase 3:** The app runs itself. Active markets monitored in real-time, alerts generated automatically, no manual intervention needed. Complete trade data from subgraphs.

**After Phase 4:** The app is intelligent. Graph-based sybil detection, news/social correlation, statistical anomaly detection, LLM-powered investigation summaries. This is what no existing open-source tool does.
