# polymarket

Detecting insider trading on [Polymarket](https://polymarket.com) by analyzing 631M+ historical order fills across 496K prediction markets.

## Project Structure

```
insider-trading/
├── pipeline/               # pure logic, no Modal imports
│   ├── scrape/             #   parallel Goldsky scraper
│   ├── ingest/             #   fetch markets, process orders into trades
│   ├── analyze/
│   │   ├── signal1/        #   statistical implausibility scoring
│   │   └── signal2/        #   timing anomaly detection
│   └── utils/              #   shared helpers
├── modal_app/              # thin Modal wrappers (import from pipeline/)
├── dashboard/              # Streamlit visualization
├── data/                   # shared artifacts (gitignored)
│   ├── scrape/             #   raw chunks
│   ├── ingest/             #   markets.csv, trades.csv, goldsky/
│   └── analyze/            #   parquet outputs
│       ├── signal1/
│       └── signal2/
└── pyproject.toml          # single config (deps: core, [modal], [dashboard])
```

To swap in test or dummy data, set the `POLYMARKET_DATA_DIR` environment variable to an alternate `data/` directory with the same subfolder structure.

## Pipeline Overview

### 1. Collect raw data

**Market metadata** (questions, tokens, outcomes) is fetched from Polymarket's Gamma API by `pipeline/ingest/markets.py`.

**Raw `OrderFilled` events** (on-chain order fills) come from two sources:
- Bulk historical data was downloaded from [warproxxx/poly_data](https://github.com/warproxxx/poly_data)
- New events since the bulk download are scraped from the [Goldsky GraphQL subgraph](https://api.goldsky.com)

`pipeline/ingest/goldsky.py` contains the original single-threaded scraper. `pipeline/scrape/scraper.py` replaces it with a parallel scraper that partitions the time range by event density. It can run locally (20 async workers) or fan out across many Modal containers (`modal_app/scrape.py`) for horizontal scaling.

### 2. Process orders into trades

Raw order fills only have token IDs and amounts. `pipeline/ingest/trades.py` joins them with market metadata to produce structured trades with market IDs, prices, buy/sell directions, and USD amounts. `pipeline/ingest/live.py` incrementally appends new trades.

`pipeline/ingest/orchestrate.py` runs the full ingest pipeline: markets → goldsky → live processing.

When using the parallel scraper, concatenate its output chunks first:

```bash
# if chunks are on Modal volume, download them first
modal volume get polymarket-data /scrape/ insider-trading/data/scrape/

# decompress and concatenate chunks into the expected input location
zcat data/scrape/chunk_00_*.csv.gz > data/ingest/goldsky/orderFilled.csv
for f in data/scrape/chunk_{01..19}_*.csv.gz; do
    zcat "$f" | tail -n +2 >> data/ingest/goldsky/orderFilled.csv
done

# then process
cd insider-trading && uv run python -m pipeline.ingest.trades
```

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

The Goldsky subgraph has 631M+ order fill events and the analysis scripts process ~33 GB of trade data. [Modal](https://modal.com) lets you run everything in the cloud with high-memory containers and parallel execution.

All `modal run` commands are run from `insider-trading/`.

### Setup

```bash
uv pip install modal
python3 -m modal setup  # authenticate via browser
```

### Scrape

```bash
# Full scrape: 5 containers x 20 workers
modal run modal_app/scrape.py

# More containers for faster scraping
modal run modal_app/scrape.py --containers 10

# Scrape a specific time range
modal run modal_app/scrape.py --start 2026-02-23 --end 2026-02-26 --containers 3 --wpc 5

# Start supports relative durations, ISO dates, ISO datetimes, or unix timestamps
modal run modal_app/scrape.py --start 7d                        # last 7 days
modal run modal_app/scrape.py --start 2026-02-23T09:08:04       # ISO datetime
modal run modal_app/scrape.py --start 1759855190                # unix ts
```

Chunks are written to the `polymarket-data` Modal volume under `/scrape/`.

### Scan (validate chunks)

```bash
# Quick scan — filenames and sizes only
modal run modal_app/scan.py

# Full scan — decompress every chunk, count rows, check time ranges
modal run modal_app/scan.py --full
```

### Analyze

```bash
# Upload data to Modal volume
modal volume put polymarket-data ./data/ingest/trades.csv /ingest/trades.csv
modal volume put polymarket-data ./data/ingest/markets.csv /ingest/markets.csv

# Run both signal pipelines
modal run modal_app/analyze.py

# Run one signal at a time
modal run modal_app/analyze.py --signal 1   # implausibility
modal run modal_app/analyze.py --signal 2   # timing
```

Signal 1 runs 8 metric scripts in parallel across separate machines. Signal 2 runs its 4-step chain sequentially. Both signals run concurrently by default.

### Download results

```bash
modal volume get polymarket-data /scrape/ insider-trading/data/scrape/
modal volume get polymarket-data /analyze/signal1/ insider-trading/data/analyze/signal1/
modal volume get polymarket-data /analyze/signal2/ insider-trading/data/analyze/signal2/
```

### Running locally

The pipeline scripts work locally without Modal:

```bash
cd insider-trading

# Run analysis
uv run python pipeline/analyze/signal1/run_all.py
uv run python pipeline/analyze/signal2/run_all.py
```
