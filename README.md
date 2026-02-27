# polymarket

Detecting insider trading on [Polymarket](https://polymarket.com) by analyzing 631M+ historical order fills across 496K prediction markets.

## Project Structure

```
insider-trading/
├── pipeline/               # pure logic, no Modal imports
│   ├── scrape/             #   data acquisition (historical, gap, markets)
│   ├── ingest/             #   consolidate events + compute trades
│   ├── analyze/
│   │   ├── signal1/        #   statistical implausibility scoring
│   │   └── signal2/        #   timing anomaly detection
│   └── utils/              #   shared helpers
├── modal_app/              # thin Modal wrappers (import from pipeline/)
├── dashboard/              # Streamlit visualization
├── data/                   # shared artifacts (gitignored)
│   ├── scrape/             #   historical.csv, chunks, markets.csv
│   ├── ingest/             #   orderFilled.csv, trades.csv
│   └── analyze/            #   parquet outputs
│       ├── signal1/
│       └── signal2/
└── pyproject.toml          # single config (deps: core, [modal], [dashboard])
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `POLYMARKET_DATA_DIR` | Base directory for all data (reads and writes) | `data/` (local) or `/vol` (Modal) |

Each pipeline stage appends its own prefix (`/scrape`, `/ingest`, `/analyze/signal{1,2}`), so a base of `/vol` produces:

```
/vol/scrape/          ← historical.csv, chunk_*.csv.gz, markets.csv
/vol/ingest/          ← orderFilled.csv, trades.csv
/vol/analyze/signal1/ ← parquet outputs
/vol/analyze/signal2/ ← parquet outputs
```

## Pipeline Overview

### 1. Scrape raw data

All data acquisition lives in `pipeline/scrape/`:

**Historical OrderFilled events** are downloaded from [warproxxx/poly_data](https://github.com/warproxxx/poly_data) by `pipeline/scrape/historical.py`. This is a one-time bulk download of on-chain order fills up to 2025-10-07.

**Gap OrderFilled events** (2025-10-07 to now) are scraped from the [Goldsky GraphQL subgraph](https://api.goldsky.com) by `pipeline/scrape/scraper.py`. It partitions the time range by event density and scrapes each partition concurrently. Runs locally (20 async workers) or fans out across Modal containers (`modal_app/scrape.py`).

**Market metadata** (questions, tokens, outcomes) is fetched from Polymarket's Gamma API by `pipeline/scrape/markets.py`.

### 2. Ingest: consolidate and process

`pipeline/ingest/consolidate.py` merges the historical CSV and scraper chunks into a single `orderFilled.csv`.

`pipeline/ingest/trades.py` joins raw order fills with market metadata to produce structured trades with market IDs, prices, buy/sell directions, and USD amounts.

### 3. Analyze trades

Two signal pipelines score wallets for suspicious behavior:

**Signal 1 — Statistical Implausibility**: Aggregates all trades into per-wallet positions, then computes 8 metrics (contrarian win rate, profit factor, niche market accuracy, Brier score, etc.) and combines them into a weighted suspicion score.

**Signal 2 — Timing Anomalies**: Builds 5-minute price history, detects price spikes (>30pp moves), finds wallets that traded in the correct direction 30min–4hrs before spikes, and scores them by hit rate.

### 4. Dashboard

A Streamlit app for exploring results: wallet leaderboard, per-wallet drill-down, timing analysis.

```bash
cd insider-trading && uv run streamlit run dashboard/app.py
```

## Running on Modal

The entire pipeline runs on [Modal](https://modal.com) with data stored on a shared Modal volume. Only the final analysis outputs need to be downloaded locally.

All `modal run` commands are run from `insider-trading/`.

### Setup

```bash
uv pip install modal
python3 -m modal setup  # authenticate via browser
```

### Step 1: Scrape

```bash
# Run everything: historical download + markets + gap scrape
modal run modal_app/scrape.py --task all

# Or run individual tasks:
modal run modal_app/scrape.py --task historical       # download bulk archive from S3
modal run modal_app/scrape.py --task markets           # fetch market metadata
modal run modal_app/scrape.py                          # gap scrape (default)

# Gap scrape options:
modal run modal_app/scrape.py --containers 10                    # 10 x 20 = 200 workers
modal run modal_app/scrape.py --start 7d                         # last 7 days
modal run modal_app/scrape.py --start 2026-02-23 --end 2026-02-26
```

Data is written to the `polymarket-data` Modal volume under `/vol/scrape/`.

### Step 2: Scan (validate chunks)

```bash
# Quick scan — filenames and sizes only
modal run modal_app/scan.py

# Full scan — decompress every chunk, count rows, check time ranges
modal run modal_app/scan.py --full
```

### Step 3: Ingest

```bash
# Consolidate raw events + process trades (reads from /scrape/, writes to /ingest/)
modal run modal_app/ingest.py
```

### Step 4: Analyze

```bash
# Run both signal pipelines
modal run modal_app/analyze.py

# Run one signal at a time
modal run modal_app/analyze.py --signal 1   # implausibility
modal run modal_app/analyze.py --signal 2   # timing
```

Signal 1 runs 8 metric scripts in parallel across separate machines. Signal 2 runs its 4-step chain sequentially. Both signals run concurrently by default.

### Download results

```bash
# Only download the analysis outputs you need
modal volume get polymarket-data /analyze/signal1/ ./data/analyze/signal1/
modal volume get polymarket-data /analyze/signal2/ ./data/analyze/signal2/
```

### Running locally

The pipeline scripts work locally without Modal:

```bash
cd insider-trading

# Scrape
uv run python -m pipeline.scrape.historical        # download bulk data
uv run python -m pipeline.scrape.markets            # fetch market metadata
uv run python pipeline/scrape/scraper.py            # gap scrape (20 async workers)

# Ingest
uv run python -m pipeline.ingest.consolidate        # merge historical + chunks
uv run python -m pipeline.ingest.trades             # process trades

# Analyze
uv run python pipeline/analyze/signal1/run_all.py
uv run python pipeline/analyze/signal2/run_all.py
```
