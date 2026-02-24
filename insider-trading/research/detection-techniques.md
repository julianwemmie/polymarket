# Insider Trading Detection Techniques for Prediction Markets & Crypto

> Research compiled February 2026. Covers academic approaches, blockchain analytics, real-world cases, signal correlation, and open-source tooling.

---

## Table of Contents

1. [Academic & Industry Approaches to Insider Trading Detection](#1-academic--industry-approaches-to-insider-trading-detection)
2. [Wallet Clustering & Sybil Detection Techniques](#2-wallet-clustering--sybil-detection-techniques)
3. [Real-World Cases of Insider Trading on Prediction Markets](#3-real-world-cases-of-insider-trading-on-prediction-markets)
4. [Signal Correlation Approaches](#4-signal-correlation-approaches)
5. [Open Source Tools & Projects](#5-open-source-tools--projects)

---

## 1. Academic & Industry Approaches to Insider Trading Detection

### 1.1 Statistical & Anomaly Detection Methods

#### Contextual Anomaly Detection
- Unsupervised ML methods identify **discontinuities in trading activity** near price-sensitive events (e.g., takeover bids, regulatory announcements)
- Approaches use **principal component analysis (PCA)** and **autoencoders** as dimensionality reduction techniques to surface anomalous trading patterns
- Paper: *"Dimensionality reduction techniques to support insider trading detection"* (arXiv:2403.00707, 2024) -- uses PCA and autoencoders for contextual anomaly detection in market surveillance

#### Statistical Distance Methods
- Design methods to identify **multivariate sequences of anomalous transactions** based on measures of statistical distance
- Detect deviations from expected trading volume, order size distributions, and temporal patterns around known events

### 1.2 Machine Learning Approaches

#### Supervised Learning
- **Random Forest and Extreme Gradient Boosting (XGBoost)** -- ensemble methods shown to improve classification accuracy for detecting unlawful insider trading
  - Paper: *"A Random Forest approach to detect and identify Unlawful Insider Trading"* (arXiv:2411.13564, 2024)
- **Deep learning approaches** -- LSTM and CNN architectures for sequence-based detection of illegal trading patterns
  - GitHub: [SheikhRabiul/A-Deep-Learning-Based-Illegal-Insider-Trading-Detection-and-Prediction-Technique-in-Stock-Market](https://github.com/SheikhRabiul/A-Deep-Learning-Based-Illegal-Insider-Trading-Detection-and-Prediction-Technique-in-Stock-Market)
- Paper: *"A machine learning approach to support decision in insider trading detection"* (EPJ Data Science, 2024, Springer) -- proposes two complementary unsupervised ML methods for market surveillance

#### Unsupervised Learning
- **K-means clustering** -- groups investors by trading behavior to find those acting anomalously relative to peers near price-sensitive events
- **Insider ring detection** -- identifies small groups of investors trading in a synchronized way in a rewarding position before a price-sensitive event (co-clustering approach)

#### Graph-Based & Network Analysis
- **Shadow trading detection** -- AMGIN (Adaptive Market Graph Intelligence Network), a graph-based deep learning framework for detecting trades on economically related but distinct securities
  - Paper: *"Shadow trading detection: A graph-based surveillance approach"* (ScienceDirect, 2025)
- **Graph Reinforcement Learning** -- models the financial ecosystem as a dynamic, heterogeneous graph capturing interactions among traders, companies, and regulators
  - Paper: *"Graph Reinforcement Learning for Insider Trading Detection"* (SSRN:5559840)
- **Network-based anomaly detection** -- identifies anomalous ego nodes that occupy bridge positions between highly connected components, indicating hubs between trading cliques
  - Paper: *"Network-based Anomaly Detection for Insider Trading"* (arXiv:1702.05809)
- **Social network analysis** -- predicts future trading decisions of retail investors based on social connections in insider networks; high predictability based on social neighborhood data may indicate exploitation of non-public information

#### NLP & Sentiment Analysis
- NLP models analyze news headlines, tweets, Reddit threads, and regulatory filings to correlate with abnormal trading patterns
- Paper: *"AI-Powered Detection of Insider Trading Activities in Financial Market"* (ResearchGate, 2025)

### 1.3 Industry Platform Approaches

#### Kalshi -- "Poirot" System
- Proprietary trade surveillance engine using **behavioral and pattern-based models** to flag unusual trades in real time
- Flags disproportionately large or oddly timed trades
- Anomalies are sent to an internal market regulation team for review (KYC data, funding sources, trade history)
- Over **200 investigations** conducted and accounts frozen in past year
- Partnered with **Solidus Labs** and **Wharton Forensic Analytics Lab** for deeper data analysis
- Formed independent **Surveillance Advisory Committee**

#### Solidus Labs -- "HALO" Platform
- Crypto-native trade surveillance and market integrity hub
- Detects spoofing, layering, wash trading, collusion by analyzing **intent, not just outcomes**
- AI-powered anomaly detection claims to eliminate **90% of false positives**
- Monitors manipulation across both on-chain and off-chain venues (CeFi/DeFi, spot/derivatives)
- GenAI-driven automation for accelerating investigations
- URL: [soliduslabs.com](https://www.soliduslabs.com/)
- Dedicated product: [Prediction Market Surveillance](https://www.soliduslabs.com/clients/prediction-market-surveillance)

#### Polysights -- "Insider Finder"
- Scans Polymarket for unusually large or abnormal trades suggesting insider knowledge
- Powered by **Vertex AI, Gemini, and Perplexity** AI models
- Flags wallets exhibiting insider characteristics: recently created, participation in few markets, large single bets, positions completed hours-to-days before events
- Typical suspicious profile: registered < 1 day, betting on 2-3 specific events, single bets > $10K, positions closed hours before outcome
- ~85% of flagged situations end profitably (per creator Tre Upshaw, ~24,000 users)
- URL: [app.polysights.xyz/insider-finder](https://app.polysights.xyz/insider-finder)

#### Compound AI (Peter Liu / Twenty Labs)
- Former Google DeepMind researcher built first-party Polymarket integration for systematic insider detection
- Built a **custom database optimized for AI agent queries** rather than relying on Polymarket's rate-limited API
- Agent architecture scales to equivalent of thousands of concurrent human analysts
- ~85% of flagged trades turned out to be winners

### 1.4 Proposed Mitigation Techniques

- **Dynamic spread widening** -- automatically widen bid-ask spread when surveillance detects suspicious activity, making it more expensive for insiders to trade
- **Position limits on new accounts** -- cap position sizes for accounts below a certain age or transaction count
- **Mandatory KYC thresholds** -- require identity verification above certain bet sizes
- **Market-level circuit breakers** -- pause trading in specific markets when anomalous volume is detected

---

## 2. Wallet Clustering & Sybil Detection Techniques

### 2.1 Core Clustering Heuristics

#### Common Input Ownership Heuristic (Co-Spend)
- **Foundational method**: if multiple addresses are used as inputs in the same transaction, they are likely controlled by the same entity (because the sender needs private keys for all input addresses)
- Used extensively by Chainalysis Reactor, Nansen, and other analytics platforms
- Limitations: **CoinJoin** transactions intentionally spoil co-spend analysis; **PayJoin** is specifically built to break this heuristic

#### Change Address Analysis
- In Bitcoin's UTXO model, identifies "change" addresses that return funds to the sender
- Heuristic signals: new address appearing for the first time as an output, round-number amounts on the non-change output, address format matching the input addresses
- Combined with co-spend analysis to expand entity clusters

#### Deposit Address Heuristic
- Identifies and clusters addresses of centralized services (exchanges, custodians)
- Starting from known deposit addresses, follows fund flows to consolidation/hot wallet addresses
- Service-specific heuristics are custom-tailored for specific entity architectures

### 2.2 Behavioral & Temporal Heuristics

#### Transaction Timing Patterns
- **Short interval detection**: flags transactions occurring within 120 seconds of each other across different wallets
- **Recurring pattern detection**: identifies regular recurring patterns exceeding 30 occurrences, indicating automation
- **Temporal correlation**: mixer services average ~32 min between transactions vs. ~10 min for exchanges vs. 100+ hours for normal wallets -- these timing signatures distinguish entity types

#### Gas Price & Fee Patterns
- Wallets controlled by the same entity or automation often use consistent gas price strategies
- Identical gas price settings, nonce management patterns, and contract interaction sequences indicate shared tooling or operators
- Time-of-day patterns in gas bidding can correlate wallets to the same timezone/operator

#### Funding Trail Analysis
- Trace the origin of funds through intermediate wallets back to exchange hot wallets or known entities
- Identify **hub-and-spoke patterns** where a single funding source distributes to multiple "fresh" wallets
- Example from Polymarket insider detection: 2-hour-old wallet traced back through intermediate wallets to exchange hot wallets

### 2.3 Sybil Detection Specific Techniques

#### On-Chain Behavioral Fingerprinting
- If an address "looks and acts" like a sybil on-chain, it likely is one
- Signals: identical contract interaction sequences, same DApps used in same order, similar token holdings, correlated deposit/withdrawal timing
- Nansen's Linea Airdrop Sybil Detection surfaced wallets acting the same way, representing automation or strong behavioral correlation

#### Graph-Based Sybil Detection
- Build transaction graphs and apply community detection algorithms (Louvain, Label Propagation)
- Identify tightly-connected clusters with similar behavioral signatures
- Use **graph neural networks (GNNs)** to classify wallet clusters as sybil vs. organic

#### Cross-Reference with Off-Chain Data
- Combine on-chain heuristics with IP addresses (where available), browser fingerprints, KYC data overlap
- Social media linkages (same Twitter/X account promoting multiple wallets)

### 2.4 Major Analytics Platforms

#### Chainalysis Reactor
- Industry-leading blockchain investigation tool
- Uses co-spend, change address, and deposit heuristics with proprietary ML extensions
- **Network-wide heuristics** (generic, applicable to any wallet) + **service-specific heuristics** (custom-tailored per entity)
- Has helped recover over $1 billion in tokens from illegal operations
- URL: [chainalysis.com/product/reactor](https://www.chainalysis.com/product/reactor/)

#### Nansen
- AI-powered wallet labeling and real-time on-chain intelligence
- Clustering algorithms to identify wallets controlled by the same entity
- Sybil filtering techniques to detect bot networks
- ML models trained on transaction data for entity classification
- Over 350 million labeled addresses across supported chains
- URL: [nansen.ai](https://www.nansen.ai/)

#### Arkham Intelligence -- "Ultra" Engine
- Proprietary AI engine that gathers data from on-chain and off-chain sources
- Synthesizes into unified database, then uses advanced algorithms to link blockchain addresses to real-world entity counterparts at scale
- Multiple data sources for entity verification: public disclosures, social media analysis, proprietary investigation techniques
- 350M+ labeled addresses, 200K+ profiled entities
- Processes millions of transactions daily across multiple blockchain networks
- URL: [arkhamintelligence.com](https://www.arkhamintelligence.com/)

#### TRM Labs
- Helps organizations avoid transactions with blacklisted entities
- Focus on compliance and sanctions screening
- Cross-chain entity resolution

---

## 3. Real-World Cases of Insider Trading on Prediction Markets

### 3.1 Venezuela/Maduro Case (January 2026)

**What happened:**
- U.S. forces seized Venezuelan President Nicolas Maduro on January 3, 2026
- An anonymous Polymarket user (handle: "Burdensome-Mix") invested >$30K and netted **$436,759 profit**
- The account was created less than one week before the capture
- Only made bets associated with Maduro's exit and U.S./Venezuela conflict

**Detection signals:**
- Fresh wallet created days before the operation
- No prior trading history
- Only targeted contracts tied to Maduro
- Polymarket listed odds at just 5.5% for Maduro capture by January 31 -- making the bet highly contrarian
- Blockchain analytics firm **Lookonchain** identified **three wallets** netting combined profit of **$630,484** ($400K + $145K + $75K)

**Regulatory response:**
- Rep. Ritchie Torres (D-NY) introduced the **Public Integrity in Financial Prediction Markets Act of 2026**
- Bill seeks to ban wagering on prediction markets by members of Congress and government insiders

### 3.2 Google "Year in Search" / AlphaRaccoon Case (December 2025)

**What happened:**
- Trader "AlphaRaccoon" (wallet 0xafEe...) turned $1.15M into ~$2M+ in under 24 hours
- Achieved **22-for-23 success rate** on Google Year in Search prediction markets
- Notably bet on singer d4vd (given 0.2% chance) -- turning $10,647 into ~$200K

**Detection signals:**
- Wallet deposited $3 million into Polymarket and immediately placed large bets
- Prior suspicious activity: in November 2025, same user pocketed >$150K by predicting exact launch day of Google Gemini 3.0
- When allegations surfaced, user changed username -- but on-chain activity remained visible
- Near-perfect accuracy on information that would only be available to Google insiders

**Status:**
- No hard proof ties AlphaRaccoon to Google as of February 2026
- No known formal investigation

### 3.3 Israeli Military Intelligence Case (June 2025 / Indicted February 2026)

**What happened:**
- An army reservist and a civilian used **classified military intelligence** to place bets on Polymarket
- The reservist accessed classified info about Israel's planned strike on Iran in June 2025
- Shared the information with a civilian who placed bets under handle "ricosuave666"
- Wagered tens of thousands of dollars, profiting ~$150K

**Detection signals:**
- Suspicious accuracy on military operation timing
- Only targeted contracts related to Israeli military operations
- Pattern of bets placed immediately before classified operations

**Enforcement:**
- Both indicted for **bribery and obstruction of justice** by Israeli prosecutors (February 2026)
- Identities remain under court-issued gag order
- First known criminal prosecution globally for prediction market insider trading based on classified information
- Israeli defense establishment stated: engaging in such betting poses "substantial security risk to IDF operations"

### 3.4 Pattern Summary Across Cases

| Signal | Maduro | AlphaRaccoon | Israel Military |
|--------|--------|-------------|-----------------|
| Fresh/new wallet | Yes | Partial | Yes |
| Low market diversity | Yes | Yes | Yes |
| Large position relative to market | Yes | Yes | Yes |
| Contrarian position (low probability bet) | Yes (5.5% odds) | Yes (0.2% odds) | Yes |
| Near-perfect accuracy | Yes | Yes (22/23) | Yes |
| Timing correlation with event | Hours before | Hours before | Days before |

### 3.5 Regulatory Landscape

- **CFTC**: ended investigation into Polymarket (July 2025) without new charges; has brought **zero insider trading enforcement actions** against prediction market traders
- **DOJ**: similarly ended Polymarket investigation without charges
- **Israel**: first jurisdiction to actually prosecute prediction market insider trading
- **U.S. Congress**: Torres bill pending; growing bipartisan interest in prediction market regulation
- **Prediction market platforms**: self-regulation via surveillance (Kalshi's Poirot, Polymarket community tools)

---

## 4. Signal Correlation Approaches

### 4.1 Trading Activity vs. External Signal Correlation

The core detection thesis: **insider trading creates a detectable temporal signature** -- abnormal trading activity that precedes public information release.

#### Key Correlation Dimensions
- **Volume spikes** before news announcements (measured against baseline volume for that market)
- **Price movement** in the "correct" direction before public information
- **New wallet creation** clustering around specific event windows
- **Position concentration** -- single wallets accumulating disproportionate share of a market's open interest

### 4.2 News & Social Media Correlation

#### Data Sources
- **News APIs**: NewsAPI, GDELT, Bloomberg Terminal API, Reuters Eikon
- **Social media**: Twitter/X API (real-time sentiment), Reddit API (r/polymarket, r/cryptocurrency), Telegram channel monitoring
- **On-chain data**: Dune Analytics, Flipside Crypto, The Graph (subgraphs)
- **Market data**: Polymarket CLOB API, CoinGecko, CoinMarketCap

#### NLP & Sentiment Tools
- **Grok AI** (via X/Twitter) -- real-time sentiment monitoring to detect hype cycles
- **LunarCrush** -- social media analytics specifically for crypto sentiment
- **Santiment** -- on-chain + social + development activity correlation
- **The TIE** -- alternative data and analytics for digital assets; NLP-based sentiment scoring

#### Academic Approaches
- Paper: *"Wisdom of the crowd signals: Predictive power of social media trading signals for cryptocurrencies"* (Electronic Markets, Springer, 2025)
- Paper: *"A decision support system using signals from social media and news to predict cryptocurrency prices"* (ScienceDirect, 2023)
- Demonstrates that social media signals on X, Reddit, and Telegram show significant correlation with digital asset price movements

### 4.3 Polymarket-Specific Data Access

#### Polymarket API Architecture
- **CLOB (Central Limit Order Book)**: hybrid-decentralized; off-chain matching, on-chain settlement via Polygon smart contracts
- **Primary endpoint**: `https://clob.polymarket.com` (Polygon chain ID 137)
- **Gamma API**: market data (listings, outcome prices, liquidity)
- **CLOB API**: trading (signed EIP-712 orders)
- **Data API**: historical trade data

#### Client Libraries
- **Python**: [Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- **Rust**: [Polymarket/rs-clob-client](https://github.com/Polymarket/rs-clob-client)
- **Unified Python (PyPI)**: `polymarket-apis` -- includes CLOB, Gamma, Data, Web3, WebSocket, and GraphQL clients

#### On-Chain Data (Polygon)
- All settlements visible on Polygon blockchain
- Can query via Polygonscan, Dune Analytics, or direct RPC
- CTF (Conditional Token Framework) contract interactions reveal positions

### 4.4 Cross-Signal Detection Pipeline

A robust detection system would correlate:

1. **On-chain signals**: new wallet creation, large trades, unusual position sizes
2. **Market microstructure signals**: order book imbalance, spread changes, liquidity shifts
3. **External timing signals**: news release timestamps, social media activity spikes, regulatory filings
4. **Behavioral signals**: wallet age, market diversity, historical accuracy, funding trail

**Detection logic**: flag when on-chain signals (1, 2) significantly precede external signals (3) with behavioral signals (4) matching known insider profiles.

---

## 5. Open Source Tools & Projects

### 5.1 Polymarket-Specific Tools

#### polymarket-insider-tracker (pselamy)
- **Repository**: [github.com/pselamy/polymarket-insider-tracker](https://github.com/pselamy/polymarket-insider-tracker)
- Detects potential insider trading by tracking suspicious wallet behavior patterns
- **Detection criteria**:
  - Fresh wallets (< 5 lifetime transactions)
  - Niche markets (< $50K daily volume)
  - Large positions (> 2% order book impact)
- **Architecture**: Polymarket API (real-time) -> Wallet Profiler (blockchain analysis) -> Anomaly Detector (ML + heuristics)
- Includes funding trail analysis (traces back to exchange hot wallets)
- Demonstrated: detected wallet turning $35K into $442K (12.6x) with 5 alerts before the event

#### polymarket-insider-bot (NickNaskida)
- **Repository**: [github.com/NickNaskida/polymarket-insider-bot](https://github.com/NickNaskida/polymarket-insider-bot)
- High-performance async bot for detecting potential insider trading on Polymarket
- Real-time monitoring with alert system

#### Polycool
- Real-time Polymarket smart trader tracking platform
- Identifies and alerts on whale/insider-like trading activity
- URL: [polymark.et/product/polycool](https://polymark.et/product/polycool)

#### PolyWallet
- Comprehensive Polymarket analytics platform
- Deep wallet analysis and trade tracking
- URL: [polymark.et/product/polywallet](https://polymark.et/product/polywallet)

#### Polymarket Agents (Official)
- **Repository**: [github.com/Polymarket/agents](https://github.com/Polymarket/agents)
- Official Polymarket repo for autonomous trading with AI agents
- Useful as a foundation for building detection agents that interact with the CLOB

### 5.2 General Blockchain Investigation & Analysis Tools

#### Forta Network
- **Decentralized real-time blockchain monitoring network** for DeFi, NFT, and Web3 security
- **Architecture**: Detection Bots (code scripts) + Scan Nodes (execute bots per transaction/block)
- Bots use both heuristic-based and ML-based approaches
- **Scam Detector**: threat intelligence on malicious smart contracts, EOAs, and URLs
- Used by Lido, Maker, Compound, Aave, Liquity
- Supports Ethereum, Polygon, Arbitrum, Optimism, Avalanche, BNB Chain, Fantom
- Premium threat intel feeds available
- **Repository & Docs**: [forta.org](https://forta.org/), [docs.forta.network](https://docs.forta.network/)

#### On-Chain Investigations Tools List
- **Repository**: [github.com/OffcierCia/On-Chain-Investigations-Tools-List](https://github.com/OffcierCia/On-Chain-Investigations-Tools-List)
- Comprehensive curated list of tools for investigating crypto hacks and security incidents
- Covers transaction tracing, wallet analysis, forensics tools

#### Awesome OSINT Blockchain Analysis
- **Repository**: [github.com/aaarghhh/awesome_osint_blockchain_analysis](https://github.com/aaarghhh/awesome_osint_blockchain_analysis)
- OSINT resources for blockchain investigations
- Tools for visualizing address relations, sanctioned address lookup
- Supports BTC, ETH, ERC20 tokens

#### GraphMule
- **Repository**: [github.com/Hunter764/GraphMule](https://github.com/Hunter764/GraphMule)
- Graph-based financial crime detection engine
- Designed to identify money muling networks, smurfing patterns, and shell accounts
- Uses advanced network analysis and graph visualizations
- Relevant techniques transferable to insider trading ring detection

#### Blockchain & Cryptocurrency Forensics
- **Repository**: [github.com/Amrita-TIFAC-Cyber-Blockchain/Blockchain-and-Cryptocurrency-Forensics](https://github.com/Amrita-TIFAC-Cyber-Blockchain/Blockchain-and-Cryptocurrency-Forensics)
- Academic forensics tools and methodologies

### 5.3 Data Platforms & APIs (Free/Freemium Tiers)

| Tool | Purpose | Access |
|------|---------|--------|
| **Dune Analytics** | SQL-based on-chain data queries | Free tier, community dashboards |
| **Flipside Crypto** | On-chain analytics with SQL interface | Free tier available |
| **The Graph** | Decentralized indexing protocol (subgraphs) | Free hosted service |
| **DeFi Llama** | TVL aggregation, protocol analytics | Fully open source |
| **Breadcrumbs** | Open blockchain analytics for tracing/monitoring | Free tier |
| **Polygonscan** | Polygon block explorer (Polymarket settlement chain) | Free API |
| **Etherscan** | Ethereum block explorer | Free API tier |
| **CoinGecko API** | Price, volume, market data | Free tier |
| **NewsAPI** | News article aggregation | Free tier (limited) |

### 5.4 Academic Code & Reproducible Research

| Paper / Project | Method | Code |
|----------------|--------|------|
| Deep Learning Insider Trading Detection (SheikhRabiul) | LSTM/CNN for stock market | [GitHub](https://github.com/SheikhRabiul/A-Deep-Learning-Based-Illegal-Insider-Trading-Detection-and-Prediction-Technique-in-Stock-Market) |
| AI-powered Fraud Detection in DeFi | Lifecycle-based ML approach | [arXiv:2308.15992](https://arxiv.org/html/2308.15992v3) |
| Volfefe - Polymarket Insider Detection | Multi-phase insider detection pipeline | [GitHub Issue #105](https://github.com/razrfly/volfefe/issues/105) |

### 5.5 Key Takeaways for Building a Detection System

**What to build on:**
- `polymarket-insider-tracker` is the most directly relevant open-source starting point -- it implements the core pipeline (API ingestion, wallet profiling, anomaly detection)
- Forta Network's bot architecture provides a model for decentralized, modular detection
- Dune Analytics community dashboards already have Polymarket-specific queries for whale tracking

**What is missing / opportunities:**
- No open-source tool currently combines on-chain analysis with news/social media temporal correlation
- Graph-based insider ring detection (from academic literature) has not been applied to prediction markets in any public tool
- Cross-market correlation (e.g., someone trading on both Polymarket and Kalshi, or hedging on DEXs) is unexplored in open-source
- Real-time alerting with LLM-powered explanation/summarization of why a trade looks suspicious
- Historical backtesting framework to validate detection heuristics against known insider cases

---

## Appendix: Key References

### Academic Papers
1. *"A machine learning approach to support decision in insider trading detection"* -- EPJ Data Science, Springer, 2024
2. *"A Random Forest approach to detect and identify Unlawful Insider Trading"* -- arXiv:2411.13564, 2024
3. *"Dimensionality reduction techniques to support insider trading detection"* -- arXiv:2403.00707, 2024
4. *"Mining Illegal Insider Trading of Stocks: A Proactive Approach"* -- arXiv:1807.00939
5. *"Shadow trading detection: A graph-based surveillance approach"* -- ScienceDirect, 2025
6. *"Graph Reinforcement Learning for Insider Trading Detection"* -- SSRN:5559840
7. *"Network-based Anomaly Detection for Insider Trading"* -- arXiv:1702.05809
8. *"Predicting the trading behavior of socially connected investors: Graph neural network approach"* -- ScienceDirect, 2023
9. *"AI-powered Fraud Detection in Decentralized Finance: A Project Life Cycle Perspective"* -- arXiv:2308.15992
10. *"Wisdom of the crowd signals: Predictive power of social media trading signals for cryptocurrencies"* -- Electronic Markets, Springer, 2025
11. *"How to Peel a Million: Validating and Expanding Bitcoin Clusters"* -- USENIX Security, 2022

### Industry Articles & Journalism
- [Tracking Insider Trading on Polymarket Is Turning Into a Business of Its Own](https://gizmodo.com/tracking-insider-trading-on-polymarket-is-turning-into-a-business-of-its-own-2000709286) -- Gizmodo, 2026
- [The Many Cases of Insider Trading on Polymarket](https://bitcoinchaser.com/cases-insider-trading-polymarket/) -- BitcoinChaser
- [Prediction Market Insider Trading: Why Polymarket and Kalshi Need Regulation](https://philippdubach.com/posts/the-absolute-insider-mess-of-prediction-markets/) -- Philipp Dubach
- [How to Solve Insider Trading in Prediction Markets](https://www.dopaminemarkets.com/p/how-to-solve-insider-trading-in-prediction) -- Dopamine Markets
- [Israel accuses two of using military secrets to place Polymarket bets](https://www.npr.org/2026/02/12/nx-s1-5712801/polymarket-bets-traders-israel-military) -- NPR, Feb 2026
- [A $400,000 profit on Maduro's capture raises insider trading questions](https://www.npr.org/2026/01/05/nx-s1-5667232/polymarket-maduro-bet-insider-trading) -- NPR, Jan 2026
- [Polymarket User Accused of $1 Million Insider Trade on Google Search Markets](https://gizmodo.com/polymarket-user-accused-of-1-million-insider-trade-on-google-search-markets-2000696258) -- Gizmodo
- [Kalshi Expands Surveillance to Fight Insider Trading](https://coinlaw.io/kalshi-surveillance-insider-trading-crackdown/) -- CoinLaw, 2026
