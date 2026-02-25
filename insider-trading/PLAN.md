# Entity-Based Insider Trading Detection — Implementation Plan

## The Problem

Current approach pulls top markets by volume and scores wallets by overall win rate. This is broken because:
1. We only see a tiny slice of each wallet's history
2. Insider knowledge is domain-specific — a Google employee who wins 8/8 Google markets but is 50/50 overall looks normal when scored globally

## The New Approach

User picks an entity (e.g., "Google"), provides search terms → we find all Polymarket markets related to that entity → pull all trades → compute per-entity win rates → compare to each wallet's overall win rate. The **delta** (entity win rate minus overall win rate) is the primary signal.

---

## Phase 1: Database Schema

**3 new models in `backend/src/models.py`:**

- **Entity** — an investigation target (name, search_terms as JSON array, status: draft/searching/ingesting/scoring/done/error, counts)
- **EntityMarket** — join table linking entity → market, with `included` bool (user can exclude irrelevant matches) and `match_term`
- **EntityWalletScore** — per-entity suspicion score for a wallet:
  - Entity-specific: `entity_markets_traded`, `entity_wins`, `entity_losses`, `entity_win_rate`, `entity_profit`
  - Overall (from full history): `overall_markets`, `overall_wins`, `overall_losses`, `overall_win_rate`
  - Signal: `win_rate_delta` (entity - overall), `suspicion_score`, `reasons` (JSON)

**Cache fields on Wallet model:** `overall_wins_cached`, `overall_losses_cached`, `overall_win_rate_cached`, `full_history_fetched_at` — skip re-fetching if cached within 24hrs.

No migration needed — `Base.metadata.create_all` handles it.

---

## Phase 2: Market Discovery

**First: validate Gamma API search capabilities.** Before building anything, manually test what query parameters the Gamma `/events` and `/markets` endpoints accept. Check if `?slug_contains=`, `?tag=`, or `?q=` work. If search is too limited, fallback plan: fetch large batches and filter client-side by question text. This is a blocker — discovery is useless if we can't find markets by keyword.

**Add to `backend/src/services/polymarket.py`:**
- `search_events(query, limit, closed)` — searches Gamma API (method TBD based on validation above)
- `search_markets(query, limit)` — searches Gamma API markets endpoint

**New file `backend/src/services/entity_discovery.py`:**
- `discover_entity_markets(search_terms)` — iterates search terms, calls both search methods, deduplicates by conditionId, annotates each market with `_match_term`
- Returns results directly (does NOT persist) — persistence happens when user confirms selection

---

## Phase 3: Entity API + Analysis Pipeline

**New router `backend/src/routers/entities.py`:**

| Endpoint | Purpose |
|---|---|
| `POST /api/entities` | Create investigation (name + search terms) |
| `GET /api/entities` | List all investigations |
| `GET /api/entities/{id}` | Get entity detail + markets + wallet scores |
| `POST /api/entities/{id}/discover` | Run market discovery, return results (not persisted yet) |
| `POST /api/entities/{id}/analyze` | Accept confirmed market list + kick off analysis (background task). Persists EntityMarket rows, then runs pipeline. |
| `GET /api/entities/{id}/progress` | SSE progress stream |
| `GET /api/entities/{id}/wallets` | Get ranked results (min_entity_markets, limit, sort) |
| `DELETE /api/entities/{id}` | Delete investigation |

Note: `PUT /markets` endpoint removed — market selection is submitted with the analyze request instead of as a separate step.

**Extract shared win/loss utility from `wallets.py`:**

The existing `/wallets/{address}/full-history` endpoint (wallets.py ~60-120) already computes win/loss per market using `get_wallet_activity()` + `get_market()`/`get_market_by_slug()`. Extract this into a shared function in a new `backend/src/services/wallet_history.py`:
- `fetch_wallet_full_history(address)` → returns list of market records with win/loss/resolved status
- Both the existing wallets router and the new entity analysis pipeline call this
- Avoids reimplementing the same resolution-lookup and win-determination logic

**New file `backend/src/tasks/entity_analysis.py`** — the pipeline:

1. Load entity + confirmed markets
2. For each market: fetch trades via `get_trades(condition_id)` (market-centric, Data API) — finds which wallets traded entity markets
3. Group trades by wallet, filter to wallets in ≥2 entity markets
4. For each qualifying wallet: compute entity-specific win/loss using only **resolved** entity markets. Unresolved markets are tracked but excluded from win rate calculation.
5. Fetch full Polymarket history via `fetch_wallet_full_history()` (wallet-centric, Activity API) to get overall win rate — uses Wallet model cache, skips if `full_history_fetched_at` < 24hrs ago
6. Score via entity scoring engine
7. Persist EntityWalletScore rows

**Two different APIs, two different purposes:**
- Step 2 uses `get_trades(condition_id)` — market-centric. Answers: "who traded this market?"
- Step 5 uses `get_wallet_activity(address)` — wallet-centric. Answers: "what is this wallet's full history?"

**Rate limiting (built-in from the start, not deferred):**
- `asyncio.Semaphore(3)` for concurrent API calls
- 0.5s delay between requests
- For an entity with 30 markets and 200 unique wallets, expect ~500+ API calls. Pipeline must be resilient to rate limits and timeouts — retry with backoff on 429s.

**New file `backend/src/services/entity_scoring.py`** — win-rate-based scoring:

- Compute `entity_win_rate` = entity wins / entity **resolved** markets
- Compute `overall_win_rate` from full Polymarket history
- Compute `delta` = entity_win_rate - overall_win_rate
- Flag if entity win rate is high AND they've traded enough resolved entity markets (≥3)
- The delta is informational context — shows whether entity performance is an outlier vs their baseline
- No weighted factors, no compound scores. Just win rates.
- If wallet has traded entity markets but none are resolved yet, score is null (insufficient data)

Register router in `backend/src/main.py`.

---

## Phase 4: Frontend

**New page `frontend/src/pages/EntitiesPage.tsx`** (becomes home):
- "New Investigation" form: entity name + tag-style search terms input
- List of past investigations with status, market count, flagged wallets

**New page `frontend/src/pages/EntityPage.tsx`:**
- **Draft state**: "Discover Markets" button → shows results with checkboxes (question, resolution, volume, match term, include/exclude) → "Run Analysis" button submits selected markets
- **Ingesting/scoring state**: progress bar with SSE. Show "X/Y markets resolved — unresolved markets excluded from scoring" notice.
- **Done state**: results table — wallet address, entity win rate, overall win rate, delta (highlighted), entity markets traded, resolved markets, profit, suspicion score. Expandable rows for per-market breakdown.

**Updates:**
- `App.tsx` — add `/entities` and `/entities/:id` routes, make entities the home
- `Layout.tsx` — nav: "Investigations" + "Markets"
- `api/client.ts` — add entity API methods + `postJson`/`putJson`/`deleteJson` helpers
- `types/index.ts` — add Entity, EntityMarket, EntityWalletScore types
- `WalletPage.tsx` — add section showing which entity investigations flagged this wallet

---

## Phase 5: Cleanup + Polish

- Progressive SSE updates as each wallet is scored
- Remove deprecated files (see "What Goes" below)
- WalletPage entity context section

---

## Implementation Order

1. **Validate Gamma API search** — manually test search parameters, confirm we can find markets by keyword
2. Schema (models.py) — add 3 new models + Wallet cache fields
3. API search methods (polymarket.py) + entity_discovery.py
4. Extract `fetch_wallet_full_history()` from wallets.py into wallet_history.py
5. Entity router CRUD + discover endpoint — test with curl
6. Analysis pipeline + scoring engine (with rate limiting) — test with curl
7. Frontend EntitiesPage + EntityPage + App.tsx routes + Layout nav
8. Frontend progress bar + results views
9. WalletPage entity context + cleanup deprecated files

---

## What Stays / Changes / Goes

**Stays:** polymarket.py (extend), markets router, wallets router + full-history, MarketPage, WalletPage (extend), MarketsPage, SuspicionBadge

**Changes:** models.py, main.py, App.tsx, Layout.tsx, client.ts, types/index.ts, wallets.py (extract shared logic)

**New:** entity_discovery.py, entity_scoring.py, entity_analysis.py, wallet_history.py, entities.py router, EntitiesPage.tsx, EntityPage.tsx

**Goes (remove after entity system is working):**
- `suspicion.py` — replaced by entity_scoring.py. Old scoring is not used by entity system.
- `ingest.py` — main ingestion flow replaced. Helper functions (`_parse_ts`, `_compute_wallet_win_loss`) moved to wallet_history.py if needed.
- `leaderboard.py` router — no longer relevant once entity investigations are the primary workflow.
- `HomePage.tsx` — replaced by EntitiesPage.tsx.
- `POST /api/ingest` + `GET /api/ingest/progress` endpoints in main.py — remove when old flow is cut.

**Note:** Old market/wallet pages and their routers stay. They still work for browsing data that entity investigations have ingested. The Wallet and Market models keep their existing `suspicion_score` fields but these become legacy — entity-based scores live in EntityWalletScore.
