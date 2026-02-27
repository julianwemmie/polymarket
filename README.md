# polymarket

Detecting insider trading on [Polymarket](https://polymarket.com) by analyzing 151M+ historical trades across 496K prediction markets.

## Pipeline Overview

The project is organized as a four-step pipeline. All data flows through a shared `data/` directory.

```
insider-trading/
├── 1-scrape/       # collect raw order events
├── 2-ingest/       # fetch markets, process orders into trades
├── 3-analyze/      # score wallets for suspicious behavior
├── 4-dashboard/    # Streamlit visualization
└── data/           # shared data (gitignored)
    ├── scrape/     #   raw chunks from step 1
    ├── ingest/     #   markets.csv, trades.csv, goldsky/
    └── analyze/    #   parquet outputs from step 3
        ├── signal1/
        └── signal2/
```

To swap in test or dummy data, set the `POLYMARKET_DATA_DIR` environment variable to an alternate `data/` directory with the same subfolder structure.

### 1. Collect raw data

**Market metadata** (questions, tokens, outcomes) is fetched from Polymarket's Gamma API by `2-ingest/update_utils/update_markets.py`.

**Raw `OrderFilled` events** (on-chain order fills) come from two sources:
- Bulk historical data was downloaded from [warproxxx/poly_data](https://github.com/warproxxx/poly_data)
- New events since the bulk download are scraped from the [Goldsky GraphQL subgraph](https://api.goldsky.com)

`2-ingest/` contains the original single-threaded scraper (`update_goldsky.py`), market fetcher, and trade processor. `1-scrape/` replaces only the Goldsky scraping step with a 20-worker async scraper that partitions the time range by event density — roughly 40x faster.

```
1-scrape/
├── scrape.py           # parallel Goldsky scraper → data/scrape/

2-ingest/
├── update_all.py       # orchestrates market fetch + goldsky scrape + trade processing
├── update_utils/
│   ├── update_markets.py    # fetch market metadata → data/ingest/markets.csv
│   ├── update_goldsky.py    # single-threaded Goldsky scraper → data/ingest/goldsky/
│   ├── process_trades.py    # batch: join orders + markets → trades
│   └── process_live.py      # incremental: append new trades
└── poly_utils/              # shared utilities (market loading, etc.)
```

### 2. Process orders into trades

Raw order fills only have token IDs and amounts. `process_trades.py` joins them with market metadata to produce structured trades with market IDs, prices, buy/sell directions, and USD amounts. `process_live.py` incrementally appends new trades.

When using the parallel scraper, concatenate its output chunks first:

```bash
# decompress and concatenate chunks into the expected input location
zcat data/scrape/chunk_00_*.csv.gz > data/ingest/goldsky/orderFilled.csv
for f in data/scrape/chunk_{01..19}_*.csv.gz; do
    zcat "$f" | tail -n +2 >> data/ingest/goldsky/orderFilled.csv
done

# then process as usual
cd insider-trading/2-ingest && uv run python update_utils/process_trades.py
```

### 3. Analyze trades

Two signal pipelines score wallets for suspicious behavior:

**Signal 1 — Statistical Implausibility**: Aggregates all trades into per-wallet positions, then computes 8 metrics (contrarian win rate, profit factor, niche market accuracy, Brier score, etc.) and combines them into a weighted suspicion score.

**Signal 2 — Timing Anomalies**: Builds 5-minute price history, detects price spikes (>30pp moves), finds wallets that traded in the correct direction 30min–4hrs before spikes, and scores them by hit rate.

```
3-analyze/
├── signal1-implausibility/   # 8 metric scripts + aggregator
├── signal2-timing/           # 4-step pipeline
└── modal_app.py              # cloud orchestrator
```

### 4. Dashboard

A Streamlit app for exploring results: wallet leaderboard, per-wallet drill-down, timing analysis.

```bash
cd insider-trading/4-dashboard && uv run streamlit run app.py
```

## Running Analysis on Modal

The analysis scripts process ~33 GB of trade data. They can run locally but are memory-constrained on machines with <32 GB RAM. [Modal](https://modal.com) lets you run them in the cloud with 64 GB RAM and parallel execution.

### Setup

```bash
pip install modal
python3 -m modal setup  # authenticate via browser
```

### Upload data

```bash
python3 -m modal volume create polymarket-data

python3 -m modal volume put polymarket-data \
    insider-trading/data/ingest/trades.csv /ingest/trades.csv

python3 -m modal volume put polymarket-data \
    insider-trading/data/ingest/markets.csv /ingest/markets.csv
```

### Run

```bash
cd insider-trading/3-analyze

# Run both signal pipelines
python3 -m modal run modal_app.py

# Run one signal at a time
python3 -m modal run modal_app.py --signal 1   # implausibility
python3 -m modal run modal_app.py --signal 2   # timing
```

Signal 1 runs 8 metric scripts in parallel across separate machines. Signal 2 runs its 4-step chain sequentially. Both signals run concurrently when using `--signal all` (default).

### Download results

```bash
python3 -m modal volume get polymarket-data /analyze/signal1/ insider-trading/data/analyze/signal1/
python3 -m modal volume get polymarket-data /analyze/signal2/ insider-trading/data/analyze/signal2/
```

### Running locally

The scripts still work locally without Modal. From each signal directory:

```bash
cd insider-trading/3-analyze/signal1-implausibility
uv run run_all.py

cd insider-trading/3-analyze/signal2-timing
uv run run_all.py
```
