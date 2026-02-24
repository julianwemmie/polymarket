# Detection Algorithm Improvement Plan

> Based on analysis of the current codebase (`suspicion.py`, `aggregator.py`, `models.py`, `ingest.py`) and the compiled research in `detection-techniques.md`. Written February 2026.

---

## Current State Summary

The detection engine (`backend/src/services/suspicion.py`) uses a `SuspicionEngine` class with a single method `score_wallet()` that computes a weighted average of four hard-coded factors:

| Factor | Weight | Implementation | Status |
|--------|--------|----------------|--------|
| Wallet age | 25% | `_score_wallet_age()` -- days between `wallet_first_seen` and `market_resolved_at` | **Broken** -- always returns 0.5 because `wallet_first_seen` is always `None` (PolygonScan disabled in `ingest.py` line 108) |
| Bet concentration | 20% | `_score_bet_concentration()` -- count of distinct markets traded | Works, but uses only data ingested so far (not the wallet's full Polymarket history) |
| Timing | 30% | `_score_timing()` -- hours between largest trade and market resolution | Works |
| Profit | 25% | `_score_profit()` -- absolute dollar profit with static thresholds | Works, but thresholds are arbitrary and not calibrated |

**Key architectural limitations:**
- Every wallet is scored in complete isolation -- no cross-wallet analysis
- No concept of "normal" behavior -- no statistical baselines
- No network/graph analysis -- cannot detect insider rings or sybil clusters
- No external signal correlation -- trades are not compared against news/event timelines
- The `Wallet.funding_source` field exists in the model but is never populated
- The ingestion pipeline caps at 200 trades per market (`ingest.py` line 177), which may miss activity in high-volume markets
- The suspicion threshold of 0.3 in `ingest.py` (line 304) is not calibrated

---

## Tier 1: Quick Wins (Dramatic Improvement, Minimal Code Changes)

### 1.1 Fix Wallet Age Scoring via Polymarket Data API

**What & why:** The wallet age factor (25% of the score) is completely non-functional. Every wallet gets a flat 0.5 because `_get_or_create_wallet()` in `ingest.py` skips the blockchain query and sets `first_seen=None`. We can approximate wallet age using data we already fetch -- the earliest trade timestamp for that wallet across all ingested markets -- without needing PolygonScan at all.

**Data required:** Earliest `Trade.timestamp` per `wallet_address` -- already in our database.

**Implementation:**

1. **`backend/src/tasks/ingest.py`** -- In the wallet scoring loop (line 273-348), after computing `wallet_market_count`, query for the wallet's earliest known trade:
   ```python
   # After line 283
   earliest_trade_result = await db.execute(
       select(func.min(Trade.timestamp))
       .where(Trade.wallet_address == wallet_addr)
   )
   earliest_trade_ts = earliest_trade_result.scalar_one_or_none()
   if earliest_trade_ts and wallet.first_seen is None:
       wallet.first_seen = earliest_trade_ts
   ```

2. **No changes needed in `suspicion.py`** -- `_score_wallet_age()` already handles `wallet_first_seen` correctly; it just never gets a real value.

**Complexity:** Small. ~10 lines of code in `ingest.py`. Immediately activates 25% of the scoring weight.

---

### 1.2 Replace Static Profit Thresholds with Market-Relative Scoring

**What & why:** The current `_score_profit()` uses absolute dollar thresholds ($100, $1K, $5K, $10K). This means a $2K profit on a $5K-volume niche market (40% of market volume) scores the same as $2K on a $5M market (0.04%). Real-world insider cases (Maduro, AlphaRaccoon) all show the signal is not absolute profit but *disproportionate* profit relative to the market.

**Data required:** `Market.volume` -- already stored in our `Market` model.

**Implementation:**

1. **`backend/src/services/suspicion.py`** -- Change `_score_profit()` signature to accept `market_volume`:
   ```python
   @staticmethod
   def _score_profit(
       trades: list[dict[str, Any]],
       market_volume: float,
   ) -> tuple[float, str]:
       total_profit = sum(float(t.get("profit", 0) or 0) for t in trades)

       if market_volume <= 0:
           # Fallback to absolute thresholds
           ...existing logic...

       profit_share = total_profit / market_volume

       if profit_share > 0.10:       # >10% of market volume as profit
           score = 1.0
       elif profit_share > 0.05:     # 5-10%
           score = 0.8
       elif profit_share > 0.02:     # 2-5%
           score = 0.5
       elif profit_share > 0.005:    # 0.5-2%
           score = 0.2
       else:
           score = 0.0

       return score, f"Profit ${total_profit:,.2f} = {profit_share:.1%} of market volume"
   ```

2. **`backend/src/services/suspicion.py`** -- Update `score_wallet()` signature to accept `market_volume` and pass it through.

3. **`backend/src/tasks/ingest.py`** -- Pass `market_volume=mapped["volume"]` to `score_wallet()` (line 294).

**Complexity:** Small. Changes to 2 files, ~20 lines total.

---

### 1.3 Add Position Size Relative to Market as a New Factor

**What & why:** Every real-world case in the research shows insiders taking positions that are disproportionately large relative to the market's open interest. The current engine has no concept of this. A wallet placing a $50K bet in a $100K market is vastly more suspicious than in a $50M market.

**Data required:** `Trade.amount` (already stored) and `Market.volume` (already stored).

**Implementation:**

1. **`backend/src/services/suspicion.py`** -- Add new scoring method:
   ```python
   @staticmethod
   def _score_position_size(
       trades: list[dict[str, Any]],
       market_volume: float,
   ) -> tuple[float, str]:
       total_position = sum(abs(float(t.get("amount", 0))) for t in trades)

       if market_volume <= 0:
           return 0.5, "Market volume unknown"

       position_share = total_position / market_volume

       if position_share > 0.10:      # >10% of market volume
           score = 1.0
       elif position_share > 0.05:    # 5-10%
           score = 0.7
       elif position_share > 0.02:    # 2-5%
           score = 0.4
       elif position_share > 0.005:
           score = 0.1
       else:
           score = 0.0

       return score, f"Position ${total_position:,.2f} = {position_share:.1%} of market volume"
   ```

2. **Rebalance weights** at the top of `suspicion.py`:
   ```python
   WEIGHT_WALLET_AGE: float = 0.20
   WEIGHT_BET_CONCENTRATION: float = 0.15
   WEIGHT_TIMING: float = 0.25
   WEIGHT_POSITION_SIZE: float = 0.15
   WEIGHT_PROFIT: float = 0.25
   ```

3. **Update `score_wallet()`** to call the new method and include it in the weighted average.

4. **Update `ingest.py`** to pass `market_volume` to `score_wallet()`.

**Complexity:** Small. ~30 lines in `suspicion.py`, ~5 lines in `ingest.py`.

---

### 1.4 Add Contrarian Signal: Bet Against Low-Probability Outcomes

**What & why:** In every major case (Maduro at 5.5% odds, d4vd at 0.2% odds), insiders bet on outcomes the market priced as highly unlikely. A wallet buying YES at price 0.05 (5% implied probability) that then resolves YES is far more suspicious than buying at 0.85. The `Trade.price` field already stores this data.

**Data required:** `Trade.price`, `Trade.side`, `Trade.outcome`, `Market.resolution` -- all already in the database.

**Implementation:**

1. **`backend/src/services/suspicion.py`** -- Add new method:
   ```python
   @staticmethod
   def _score_contrarian(
       trades: list[dict[str, Any]],
       resolution: str,
   ) -> tuple[float, str]:
       """Score based on whether the wallet bet on low-probability outcomes
       that ended up winning."""
       resolved_yes = resolution.lower() in ("yes", "1")

       weighted_contrarian = 0.0
       total_amount = 0.0

       for t in trades:
           price = float(t.get("price", 0.5))
           amount = abs(float(t.get("amount", 0)))
           side = t.get("side", "BUY").upper()
           outcome = str(t.get("outcome", "")).lower()

           if side != "BUY" or amount == 0:
               continue

           # Did this trade bet on the winning side?
           bet_on_winner = (
               (outcome in ("yes", "1") and resolved_yes) or
               (outcome in ("no", "0") and not resolved_yes)
           )

           if bet_on_winner:
               # Lower price = more contrarian = more suspicious
               # price=0.05 -> contrarian_factor=0.95
               contrarian_factor = max(0.0, 1.0 - price)
               weighted_contrarian += contrarian_factor * amount

           total_amount += amount

       if total_amount == 0:
           return 0.0, "No qualifying trades for contrarian analysis"

       avg_contrarian = weighted_contrarian / total_amount

       if avg_contrarian > 0.80:    # Avg buy price < 0.20
           score = 1.0
       elif avg_contrarian > 0.60:  # Avg buy price < 0.40
           score = 0.7
       elif avg_contrarian > 0.40:
           score = 0.3
       else:
           score = 0.0

       return score, f"Contrarian score: {avg_contrarian:.2f} (lower buy prices on winning side = more suspicious)"
   ```

2. **Update weights, `score_wallet()` signature, and caller in `ingest.py`** to include `resolution` parameter and the new factor.

   Suggested new weights with both 1.3 and 1.4 included:
   ```python
   WEIGHT_WALLET_AGE: float = 0.15
   WEIGHT_BET_CONCENTRATION: float = 0.10
   WEIGHT_TIMING: float = 0.20
   WEIGHT_POSITION_SIZE: float = 0.15
   WEIGHT_PROFIT: float = 0.15
   WEIGHT_CONTRARIAN: float = 0.25
   ```

**Complexity:** Small-medium. ~40 lines in `suspicion.py`, minor changes in `ingest.py`.

---

### 1.5 Calibrate Suspicion Threshold Using Known Cases

**What & why:** The current threshold of 0.3 in `ingest.py` line 304 was chosen arbitrarily. We should backtest against known insider cases (Maduro wallets, AlphaRaccoon wallet 0xafEe..., ricosuave666) to find the threshold that correctly flags known insiders while minimizing false positives.

**Data required:** Wallet addresses from the cases documented in `detection-techniques.md`. These are publicly known on-chain addresses.

**Implementation:**

1. **Create `backend/src/tasks/backtest.py`** -- A script that:
   - Ingests the specific markets from known insider cases
   - Runs the scoring engine against all wallets in those markets
   - Reports where the known-insider wallets rank
   - Computes precision/recall at various thresholds (0.2, 0.3, 0.4, ..., 0.9)
   - Outputs a recommended threshold

2. **No production code changes** until backtest results are in, then update the threshold constant in `ingest.py`.

**Complexity:** Medium. New file (~100-150 lines), but purely a development/calibration tool.

---

## Tier 2: Medium-Effort Improvements (New Detection Dimensions)

### 2.1 Win Rate Anomaly Detection

**What & why:** Normal traders have win rates around 50-55%. A wallet with a 90%+ win rate across multiple markets is statistically anomalous. AlphaRaccoon had a 22/23 (95.6%) win rate. This is a powerful signal that requires no external data -- just our existing trade and resolution data.

**Data required:** All trades for a wallet across all markets, plus market resolution data. Already in our database (`Trade` + `Market` tables).

**Implementation:**

1. **`backend/src/models.py`** -- Add fields to `Wallet`:
   ```python
   win_count: Mapped[int] = mapped_column(Integer, default=0)
   loss_count: Mapped[int] = mapped_column(Integer, default=0)
   win_rate: Mapped[float] = mapped_column(Float, default=0.0)
   ```

2. **`backend/src/services/suspicion.py`** -- Add method:
   ```python
   @staticmethod
   def _score_win_rate(
       win_rate: float,
       total_bets: int,
   ) -> tuple[float, str]:
       """Score based on anomalous win rate. Only meaningful with enough bets."""
       if total_bets < 3:
           return 0.0, f"Too few resolved bets ({total_bets}) for win rate analysis"

       # Use a simple binomial test intuition:
       # With 3+ bets, high win rates become increasingly suspicious
       if win_rate >= 0.95 and total_bets >= 5:
           score = 1.0
       elif win_rate >= 0.85 and total_bets >= 4:
           score = 0.8
       elif win_rate >= 0.75 and total_bets >= 5:
           score = 0.5
       elif win_rate >= 0.70:
           score = 0.2
       else:
           score = 0.0

       return score, f"Win rate: {win_rate:.0%} across {total_bets} resolved bets"
   ```

3. **`backend/src/tasks/ingest.py`** -- After scoring trades in a market, update wallet win/loss counts:
   ```python
   # After profit calculation, determine if this wallet won in this market
   wallet_profit = sum(t["profit"] for t in trades_for_wallet)
   if wallet_profit > 0:
       wallet.win_count += 1
   elif wallet_profit < 0:
       wallet.loss_count += 1
   total_bets = wallet.win_count + wallet.loss_count
   wallet.win_rate = wallet.win_count / total_bets if total_bets > 0 else 0.0
   ```

**Complexity:** Medium. Schema migration needed, ~50 lines across 3 files.

---

### 2.2 Funding Source Trail Analysis

**What & why:** The research shows that insider wallets frequently trace back to the same funding source through intermediate wallets (hub-and-spoke pattern). The `Wallet.funding_source` field already exists in our model but is never populated. Activating PolygonScan integration (or using the Polygon RPC directly) to populate this field enables a powerful clustering signal.

**Data required:** On-chain transaction history from Polygon. The `BlockchainClient` in `backend/src/services/blockchain.py` already has `get_funding_source()` and `get_wallet_creation_date()` implemented but disabled.

**Implementation:**

1. **`backend/src/config.py`** -- Ensure `polygonscan_api_key` is set to a real key (currently `"demo"`). Document that users need a free PolygonScan API key.

2. **`backend/src/tasks/ingest.py`** -- Re-enable blockchain queries in `_get_or_create_wallet()`. Replace the comment block at line 108-118:
   ```python
   if wallet is None:
       # Query blockchain for wallet metadata
       first_seen = None
       funding_source = None
       try:
           first_seen = await blockchain_client.get_wallet_creation_date(addr)
           funding_source = await blockchain_client.get_funding_source(addr)
       except Exception:
           logger.warning("Blockchain query failed for %s, using defaults", addr)

       wallet = Wallet(
           address=addr,
           first_seen=first_seen,
           market_count=0,
           total_volume=0.0,
           total_profit=0.0,
           suspicion_score=0.0,
           funding_source=funding_source,
       )
       db.add(wallet)
   ```

3. **`backend/src/services/suspicion.py`** -- Add funding source factor:
   ```python
   @staticmethod
   def _score_funding_source(
       funding_source: str | None,
       known_suspicious_funders: set[str],
   ) -> tuple[float, str]:
       if funding_source is None:
           return 0.3, "Funding source unknown"
       if funding_source.lower() in known_suspicious_funders:
           return 1.0, f"Funded by known suspicious wallet: {funding_source[:10]}..."
       return 0.0, f"Funded by: {funding_source[:10]}..."
   ```

4. **New module: `backend/src/services/wallet_cluster.py`** -- Build a simple in-memory graph of funding relationships:
   ```python
   class FundingCluster:
       """Groups wallets that share a common funding source."""

       def __init__(self):
           self._funder_to_wallets: dict[str, set[str]] = {}

       def register(self, wallet: str, funding_source: str | None):
           if funding_source:
               self._funder_to_wallets.setdefault(
                   funding_source.lower(), set()
               ).add(wallet.lower())

       def get_cluster(self, wallet: str) -> set[str]:
           """Return all wallets funded by the same source as `wallet`."""
           ...

       def get_suspicious_funders(self, min_funded: int = 3) -> set[str]:
           """Return funders that have funded >= min_funded wallets
           trading on Polymarket. Hub-and-spoke pattern."""
           return {
               funder for funder, wallets in self._funder_to_wallets.items()
               if len(wallets) >= min_funded
           }
   ```

**Complexity:** Medium. Requires a valid PolygonScan API key. Adds rate limiting concerns (~0.22s per wallet means ~45s for 200 wallets). Consider batching or making it a background task.

---

### 2.3 Temporal Clustering: Detect Coordinated Trading

**What & why:** The research identifies "insider ring detection" -- small groups of wallets trading in a synchronized way before events. If 5 wallets all place large bets on the same low-probability outcome within a 2-hour window in a niche market, that is far more suspicious than one wallet doing the same. The current engine scores each wallet in isolation and misses this entirely.

**Data required:** All trades in a market with timestamps -- already in our `Trade` table.

**Implementation:**

1. **New module: `backend/src/services/temporal_cluster.py`**:
   ```python
   from datetime import timedelta
   from collections import defaultdict

   class TemporalClusterDetector:
       """Detects clusters of wallets placing similar trades within a time window."""

       def detect_clusters(
           self,
           trades: list[Trade],
           window: timedelta = timedelta(hours=2),
           min_cluster_size: int = 3,
       ) -> list[dict]:
           """Group trades by outcome+side, then find temporal clusters.

           Returns list of clusters, each containing:
           - wallet_addresses: set of wallets in the cluster
           - window_start/end: the time window
           - total_volume: combined volume
           - outcome: the shared outcome
           """
           # Group trades by (outcome, side)
           by_direction = defaultdict(list)
           for trade in trades:
               key = (trade.outcome, trade.side)
               by_direction[key].append(trade)

           clusters = []
           for key, group_trades in by_direction.items():
               group_trades.sort(key=lambda t: t.timestamp)
               # Sliding window
               for i, anchor in enumerate(group_trades):
                   window_trades = [
                       t for t in group_trades[i:]
                       if t.timestamp - anchor.timestamp <= window
                   ]
                   unique_wallets = {t.wallet_address for t in window_trades}
                   if len(unique_wallets) >= min_cluster_size:
                       clusters.append({
                           "wallet_addresses": unique_wallets,
                           "window_start": anchor.timestamp,
                           "window_end": window_trades[-1].timestamp,
                           "total_volume": sum(t.amount for t in window_trades),
                           "outcome": key[0],
                           "side": key[1],
                       })

           return clusters
   ```

2. **`backend/src/tasks/ingest.py`** -- After scoring individual wallets, run cluster detection on the market's trades. Apply a score boost to wallets found in clusters:
   ```python
   # After the per-wallet scoring loop
   clusters = temporal_detector.detect_clusters(
       [t for t in market.trades],  # from ORM
       window=timedelta(hours=2),
       min_cluster_size=3,
   )
   for cluster in clusters:
       for addr in cluster["wallet_addresses"]:
           # Boost suspicion score for clustered wallets
           ...
   ```

3. **`backend/src/models.py`** -- Optionally add a `SuspicionFlag.cluster_id` field to link flags that are part of the same detected cluster.

**Complexity:** Medium. New module (~80 lines), integration into `ingest.py` (~30 lines), optional schema change.

---

### 2.4 Market-Level Baseline Comparison

**What & why:** Currently there is no concept of "what is normal for this market." A $5K trade in a $10K-volume market is anomalous; the same trade in a $10M market is noise. We need per-market statistical baselines for trade size, trade frequency, and wallet count.

**Data required:** All trades in a market -- already available.

**Implementation:**

1. **`backend/src/services/suspicion.py`** -- Add a market-level stats calculator:
   ```python
   @staticmethod
   def _compute_market_stats(
       all_trades: list[dict[str, Any]],
   ) -> dict[str, float]:
       amounts = [abs(float(t.get("amount", 0))) for t in all_trades]
       if not amounts:
           return {"mean": 0, "std": 0, "median": 0, "p95": 0}

       amounts.sort()
       n = len(amounts)
       mean = sum(amounts) / n
       variance = sum((a - mean) ** 2 for a in amounts) / n
       std = variance ** 0.5
       median = amounts[n // 2]
       p95 = amounts[int(n * 0.95)]

       return {"mean": mean, "std": std, "median": median, "p95": p95}
   ```

2. **Update individual scoring methods** to use z-scores instead of absolute thresholds. For example, in `_score_profit()`:
   ```python
   # A wallet whose profit is >3 standard deviations above the market mean
   # is statistically anomalous
   if market_stats["std"] > 0:
       z_score = (total_profit - market_stats["mean"]) / market_stats["std"]
       if z_score > 3.0:
           score = 1.0
       elif z_score > 2.0:
           score = 0.7
       ...
   ```

3. **`backend/src/tasks/ingest.py`** -- Compute market stats before the per-wallet scoring loop and pass them into `score_wallet()`.

**Complexity:** Medium. ~60 lines in `suspicion.py`, ~15 lines in `ingest.py`. No schema changes. No external dependencies.

---

### 2.5 Multi-Trade Timing Pattern Analysis

**What & why:** The current `_score_timing()` only looks at the single largest trade. But insiders often spread activity across multiple trades. A wallet that places 10 trades all within 6 hours of resolution, steadily increasing position size, is more suspicious than one large trade 2 days out. We should analyze the full trade sequence.

**Data required:** All trades for a wallet in a market with timestamps -- already available.

**Implementation:**

1. **`backend/src/services/suspicion.py`** -- Replace `_score_timing()` with a richer version:
   ```python
   @staticmethod
   def _score_timing(
       trades: list[dict[str, Any]],
       market_resolved_at: datetime,
   ) -> tuple[float, str]:
       if not trades:
           return 0.0, "No trades found"

       # Score ALL trades, not just the largest
       # Weight each trade's timing score by its dollar amount
       total_weighted_score = 0.0
       total_amount = 0.0

       for trade in trades:
           amount = abs(float(trade.get("amount", 0)))
           ts = trade.get("timestamp")
           if not isinstance(ts, datetime) or amount == 0:
               continue

           hours = (market_resolved_at - ts).total_seconds() / 3600

           if hours <= 6:
               t_score = 1.0
           elif hours <= 24:
               t_score = 0.85
           elif hours <= 72:
               t_score = 0.6
           elif hours <= 168:
               t_score = 0.3
           else:
               t_score = 0.05

           total_weighted_score += t_score * amount
           total_amount += amount

       if total_amount == 0:
           return 0.0, "No valid trade amounts"

       weighted_avg = total_weighted_score / total_amount

       # Bonus: detect escalation pattern (increasing trade sizes closer to resolution)
       sorted_trades = sorted(
           [(t.get("timestamp"), abs(float(t.get("amount", 0)))) for t in trades
            if isinstance(t.get("timestamp"), datetime)],
           key=lambda x: x[0]
       )
       if len(sorted_trades) >= 3:
           # Check if later trades are larger (escalation)
           first_half = sorted_trades[:len(sorted_trades)//2]
           second_half = sorted_trades[len(sorted_trades)//2:]
           avg_first = sum(a for _, a in first_half) / len(first_half)
           avg_second = sum(a for _, a in second_half) / len(second_half)
           if avg_second > avg_first * 2:
               weighted_avg = min(1.0, weighted_avg + 0.15)

       return weighted_avg, f"Volume-weighted timing score: {weighted_avg:.2f} (across {len(trades)} trades)"
   ```

**Complexity:** Small-medium. ~50 lines replacing existing method. No schema changes. Drop-in replacement.

---

## Tier 3: Ambitious Improvements (New Infrastructure Required)

### 3.1 Cross-Wallet Graph Analysis and Sybil Detection

**What & why:** The research identifies graph-based sybil detection as a critical capability. Insiders frequently operate through multiple wallets (the Maduro case involved at least 3 wallets netting a combined $630K). A graph that connects wallets through shared funding sources, similar trading patterns, and temporal correlation can expose these rings.

**Data required:**
- Wallet-to-wallet fund flows (from PolygonScan or Polygon RPC -- partially available via `BlockchainClient`)
- All trade data across markets (already in DB)
- Shared funding source data (from improvement 2.2)

**Implementation:**

1. **New model: `backend/src/models.py`** -- Add `WalletEdge` table:
   ```python
   class WalletEdge(Base):
       __tablename__ = "wallet_edges"

       id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
       source_address: Mapped[str] = mapped_column(String, ForeignKey("wallets.address"))
       target_address: Mapped[str] = mapped_column(String, ForeignKey("wallets.address"))
       edge_type: Mapped[str] = mapped_column(String)  # "funding", "temporal_correlation", "behavioral_similarity"
       weight: Mapped[float] = mapped_column(Float, default=1.0)
       metadata: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON
       created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
   ```

2. **New module: `backend/src/services/graph_analysis.py`**:
   ```python
   import networkx as nx
   from collections import defaultdict

   class WalletGraphAnalyzer:
       """Builds and analyzes a wallet relationship graph."""

       def __init__(self):
           self.graph = nx.Graph()

       async def build_graph(self, db: AsyncSession):
           """Load wallet edges from DB and construct networkx graph."""
           edges = await db.execute(select(WalletEdge))
           for edge in edges.scalars().all():
               self.graph.add_edge(
                   edge.source_address,
                   edge.target_address,
                   type=edge.edge_type,
                   weight=edge.weight,
               )

       def detect_clusters(self, min_size: int = 3) -> list[set[str]]:
           """Use Louvain community detection to find wallet clusters."""
           communities = nx.community.louvain_communities(self.graph)
           return [c for c in communities if len(c) >= min_size]

       def get_cluster_suspicion_boost(
           self, wallet: str, cluster: set[str], wallet_scores: dict[str, float]
       ) -> float:
           """Boost a wallet's score if it belongs to a suspicious cluster."""
           cluster_avg = sum(wallet_scores.get(w, 0) for w in cluster) / len(cluster)
           if cluster_avg > 0.5 and len(cluster) >= 3:
               return min(0.3, cluster_avg * 0.3)  # Up to 0.3 boost
           return 0.0
   ```

3. **New dependency:** `networkx` added to `requirements.txt`.

4. **Integration in `ingest.py`:** After all per-wallet scoring is done, run graph analysis as a post-processing step and apply cluster boosts.

**Complexity:** Large. New table (migration), new dependency, new module (~150 lines), significant changes to `ingest.py` for the two-pass approach. Graph must be rebuilt periodically.

---

### 3.2 External Signal Correlation: News/Event Timeline

**What & why:** The core thesis from the research is that insider trading creates a detectable temporal signature -- abnormal trading activity that *precedes* public information release. Correlating trade timestamps against news publication timestamps is the single highest-value detection signal. None of the open-source tools currently do this.

**Data required:**
- Trade timestamps (already in DB)
- News/event publication timestamps (NEW -- requires external API)
- Market-to-topic mapping (partially available via `Market.entity`)

**Implementation:**

1. **New model fields in `backend/src/models.py`**:
   ```python
   class Market(Base):
       ...
       # New fields
       first_news_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
       news_source: Mapped[str | None] = mapped_column(String, nullable=True)
   ```

2. **New module: `backend/src/services/news_correlator.py`**:
   ```python
   import httpx

   class NewsCorrelator:
       """Correlates market events with news publication timestamps."""

       def __init__(self, newsapi_key: str):
           self.newsapi_key = newsapi_key
           self._client = httpx.AsyncClient(timeout=30.0)

       async def find_earliest_news(
           self, query: str, before: datetime, after: datetime
       ) -> dict | None:
           """Search NewsAPI for the earliest article matching `query`
           published between `after` and `before`."""
           resp = await self._client.get(
               "https://newsapi.org/v2/everything",
               params={
                   "q": query,
                   "from": after.isoformat(),
                   "to": before.isoformat(),
                   "sortBy": "publishedAt",
                   "apiKey": self.newsapi_key,
                   "pageSize": 5,
               },
           )
           articles = resp.json().get("articles", [])
           if articles:
               return {
                   "published_at": articles[0]["publishedAt"],
                   "title": articles[0]["title"],
                   "source": articles[0]["source"]["name"],
               }
           return None

       def score_pre_news_trading(
           self,
           trades: list[dict],
           first_news_at: datetime,
       ) -> tuple[float, str]:
           """Score based on how much trading occurred before news broke."""
           pre_news_volume = sum(
               abs(float(t.get("amount", 0)))
               for t in trades
               if isinstance(t.get("timestamp"), datetime) and t["timestamp"] < first_news_at
           )
           total_volume = sum(abs(float(t.get("amount", 0))) for t in trades)

           if total_volume == 0:
               return 0.0, "No trade volume"

           pre_news_ratio = pre_news_volume / total_volume

           if pre_news_ratio > 0.80:
               score = 1.0
           elif pre_news_ratio > 0.50:
               score = 0.7
           elif pre_news_ratio > 0.20:
               score = 0.3
           else:
               score = 0.0

           return score, f"{pre_news_ratio:.0%} of volume placed before news broke"
   ```

3. **`backend/src/config.py`** -- Add `newsapi_key: str = ""` to `Settings`.

4. **`backend/src/tasks/ingest.py`** -- After ingesting a market, query for related news and store `first_news_at`. Use it as a scoring factor.

**Complexity:** Large. New external dependency (NewsAPI account, rate limits), new module, schema migration, and the query-to-market-topic mapping is non-trivial (the `Market.entity` field helps but is not a perfect search query).

---

### 3.3 Historical Baseline Model (Statistical Anomaly Detection)

**What & why:** Instead of hand-tuned thresholds, build a statistical model of "normal" trading behavior from historical data and flag wallets that deviate significantly. This is the approach used by Solidus Labs (HALO) and Kalshi (Poirot).

**Data required:** A large corpus of historical trades across many markets (at least 500+ markets) to establish baselines. Our current ingestion pipeline can provide this by running with `--limit 500+`.

**Implementation:**

1. **New module: `backend/src/services/baseline.py`**:
   ```python
   import numpy as np
   from scipy import stats

   class BaselineModel:
       """Maintains statistical baselines for 'normal' trading behavior."""

       def __init__(self):
           self.distributions = {}  # feature_name -> fitted distribution

       async def fit(self, db: AsyncSession):
           """Compute baseline distributions from all historical data."""
           # Compute per-wallet-market features for all non-flagged wallets
           # Features: profit_ratio, position_share, timing_hours, market_count, win_rate
           ...

           # Fit distributions
           for feature, values in feature_vectors.items():
               # Use kernel density estimation or fit a parametric distribution
               self.distributions[feature] = {
                   "mean": np.mean(values),
                   "std": np.std(values),
                   "percentiles": np.percentile(values, [90, 95, 99]),
               }

       def score_anomaly(self, features: dict[str, float]) -> tuple[float, str]:
           """Score how anomalous a wallet's features are vs. baseline."""
           anomaly_scores = []
           for feature, value in features.items():
               if feature in self.distributions:
                   dist = self.distributions[feature]
                   if dist["std"] > 0:
                       z = (value - dist["mean"]) / dist["std"]
                       # Convert z-score to 0-1 anomaly score
                       anomaly_scores.append(min(1.0, max(0.0, (abs(z) - 1) / 3)))

           if not anomaly_scores:
               return 0.0, "No baseline data available"

           combined = sum(anomaly_scores) / len(anomaly_scores)
           return combined, f"Statistical anomaly score: {combined:.2f} ({len(anomaly_scores)} features)"
   ```

2. **New dependencies:** `numpy`, `scipy` added to `requirements.txt`.

3. **New task: `backend/src/tasks/train_baseline.py`** -- A periodic job that recomputes baselines from all historical data.

4. **Integration:** The anomaly score becomes an additional factor in `score_wallet()`, or it can be used as a replacement for the entire hand-tuned scoring system.

**Complexity:** Large. Requires substantial historical data, two new dependencies, training pipeline, and potentially a different scoring architecture (anomaly score could replace or supplement the weighted-average approach).

---

### 3.4 LLM-Powered Investigation Summarization

**What & why:** When a wallet is flagged, an analyst needs to review it. An LLM can synthesize all available signals (trade history, funding trail, timing patterns, cluster membership, news correlation) into a human-readable investigation brief. This is what Compound AI and Solidus Labs are doing.

**Data required:** All signals from the improvements above, plus the market question/context.

**Implementation:**

1. **New module: `backend/src/services/llm_summarizer.py`**:
   ```python
   import httpx

   class InvestigationSummarizer:
       """Generates human-readable investigation summaries for flagged wallets."""

       def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
           self.api_key = api_key
           self.model = model

       async def summarize(
           self,
           wallet_address: str,
           flags: list[SuspicionFlag],
           trades: list[Trade],
           wallet: Wallet,
           related_wallets: list[str],  # from cluster analysis
           news_context: dict | None,
       ) -> str:
           """Generate an investigation summary."""
           prompt = self._build_prompt(wallet_address, flags, trades, wallet, related_wallets, news_context)

           async with httpx.AsyncClient() as client:
               resp = await client.post(
                   "https://api.anthropic.com/v1/messages",
                   headers={
                       "x-api-key": self.api_key,
                       "anthropic-version": "2023-06-01",
                   },
                   json={
                       "model": self.model,
                       "max_tokens": 1024,
                       "messages": [{"role": "user", "content": prompt}],
                   },
               )
               return resp.json()["content"][0]["text"]
   ```

2. **New API endpoint** in a router for on-demand investigation summaries.

3. **`backend/src/config.py`** -- Add `anthropic_api_key: str = ""` to `Settings`.

**Complexity:** Large. New external dependency (LLM API), cost per invocation, prompt engineering, and the quality depends heavily on the richness of upstream signals.

---

## Implementation Priority & Dependency Map

```
Tier 1 (do first, in this order):
  1.1 Fix wallet age ──────────────────────────── standalone, immediate win
  1.2 Market-relative profit ──────────────────── standalone, immediate win
  1.3 Position size factor ────────────────────── standalone, pairs with 1.2
  1.4 Contrarian signal ───────────────────────── standalone, highest-value new signal
  1.5 Backtest framework ──────────────────────── depends on 1.1-1.4 being done first

Tier 2 (do next):
  2.4 Market baselines ────────────────────────── standalone, improves all scoring
  2.5 Multi-trade timing ──────────────────────── standalone, replaces existing method
  2.1 Win rate anomaly ────────────────────────── standalone, new schema fields
  2.2 Funding source trail ────────────────────── requires PolygonScan API key
  2.3 Temporal clustering ─────────────────────── standalone, new module

Tier 3 (do when Tier 1+2 are stable):
  3.1 Graph analysis ──────────────────────────── depends on 2.2, 2.3
  3.2 News correlation ────────────────────────── requires NewsAPI key, partially standalone
  3.3 Statistical baseline ────────────────────── requires large historical dataset
  3.4 LLM summarization ──────────────────────── depends on all of the above for richest output
```

---

## Revised Weight Configuration After Tier 1 + Tier 2

Once improvements 1.1 through 2.5 are implemented, the `score_wallet()` method should use these factors and weights:

```python
# backend/src/services/suspicion.py

WEIGHT_WALLET_AGE: float       = 0.08   # Fixed via 1.1
WEIGHT_BET_CONCENTRATION: float = 0.08  # Existing
WEIGHT_TIMING: float           = 0.15   # Enhanced via 2.5
WEIGHT_POSITION_SIZE: float    = 0.12   # New via 1.3
WEIGHT_PROFIT: float           = 0.10   # Market-relative via 1.2
WEIGHT_CONTRARIAN: float       = 0.20   # New via 1.4 -- highest signal from case studies
WEIGHT_WIN_RATE: float         = 0.12   # New via 2.1
WEIGHT_MARKET_ANOMALY: float   = 0.15   # New via 2.4 -- z-score based
# Total: 1.00
```

The contrarian signal gets the highest weight because it is the single most discriminating factor in every documented insider case: insiders buy outcomes priced far below their true probability because they possess non-public information about the resolution.

---

## Estimated Impact

| Improvement | False Positive Reduction | True Positive Gain | Effort |
|-------------|-------------------------|-------------------|--------|
| 1.1 Fix wallet age | Low | Medium (activates 25% of dead weight) | 1-2 hours |
| 1.2 Market-relative profit | High (stops flagging $2K trades in $10M markets) | Medium | 1-2 hours |
| 1.3 Position size | Medium | High | 2-3 hours |
| 1.4 Contrarian signal | Low | Very High (catches Maduro, AlphaRaccoon patterns) | 3-4 hours |
| 1.5 Backtest | N/A (tooling) | N/A (enables calibration) | 4-6 hours |
| 2.1 Win rate | Medium | High (catches AlphaRaccoon pattern) | 4-6 hours |
| 2.2 Funding trail | Low | High (catches sybil/ring patterns) | 6-8 hours |
| 2.3 Temporal clustering | Medium | Very High (catches Maduro 3-wallet pattern) | 6-8 hours |
| 2.4 Market baselines | Very High | Medium | 4-6 hours |
| 2.5 Multi-trade timing | Medium | Medium | 2-3 hours |
| 3.1 Graph analysis | High | Very High | 2-3 days |
| 3.2 News correlation | Medium | Very High | 2-3 days |
| 3.3 Statistical baseline | Very High | High | 3-5 days |
| 3.4 LLM summarization | N/A (UX) | N/A (UX) | 1-2 days |

---

## Files Changed Summary

**Tier 1 changes (4 existing files, 1 new file):**
- `backend/src/services/suspicion.py` -- New methods, updated weights, updated `score_wallet()` signature
- `backend/src/tasks/ingest.py` -- Pass new parameters to `score_wallet()`, wallet age fix
- `backend/src/models.py` -- No changes needed for Tier 1
- `backend/src/services/aggregator.py` -- No changes needed for Tier 1
- `backend/src/tasks/backtest.py` -- NEW file for calibration

**Tier 2 additions (2 existing files modified, 2 new files):**
- `backend/src/models.py` -- Add `win_count`, `loss_count`, `win_rate` to `Wallet`
- `backend/src/tasks/ingest.py` -- Integrate new scoring, re-enable blockchain queries
- `backend/src/services/temporal_cluster.py` -- NEW
- `backend/src/services/wallet_cluster.py` -- NEW

**Tier 3 additions (3 existing files modified, 4 new files):**
- `backend/src/models.py` -- Add `WalletEdge` table, `Market.first_news_at`
- `backend/src/config.py` -- Add `newsapi_key`, `anthropic_api_key`
- `backend/src/tasks/ingest.py` -- Integrate graph analysis, news correlation
- `backend/src/services/graph_analysis.py` -- NEW
- `backend/src/services/news_correlator.py` -- NEW
- `backend/src/services/baseline.py` -- NEW
- `backend/src/services/llm_summarizer.py` -- NEW
