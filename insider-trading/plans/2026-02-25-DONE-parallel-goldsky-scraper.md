# Parallel Goldsky Scraper Plan

## Problem
- Historical data ends at 2025-10-07
- Gap to now: ~141 days, ~300M events, ~73GB uncompressed
- Sequential scraping: ~49 hours
- Need to fill the gap fast and store efficiently

## Measured Constraints
- Goldsky max batch size: **1000** (hard limit, tested)
- Query time: **~0.5s** per batch (consistent)
- Event density across gap:
  - Oct 2025: ~700K events/day
  - Nov 2025: ~1M events/day
  - Dec 2025: ~1.5M events/day
  - Jan 2026: ~3M events/day
  - Feb 2026: ~4.3M events/day

## Architecture

### Time-Range Partitioning
Split the 141-day gap into ~20 non-overlapping time windows. Each worker owns one window and scrapes independently.

```
Worker 1:  Oct 07 - Oct 14  (low density, fast)
Worker 2:  Oct 14 - Oct 21
...
Worker 20: Feb 18 - Feb 25  (high density, slower)
```

Windows should NOT be equal-length — later windows have higher event density. Use **equal-event partitioning**: probe density at multiple points, then size windows so each worker handles roughly the same number of events (~15M each).

### Per-Worker Logic
1. Query: `orderFilledEvents(where: {timestamp_gte: START, timestamp_lt: END}, orderBy: timestamp, orderDirection: asc, first: 1000)`
2. Paginate using same cursor logic as existing scraper (sticky timestamps for same-second batches)
3. Write rows to a gzipped CSV chunk: `chunk_NN_STARTDATE_ENDDATE.csv.gz`
4. Save cursor state per worker for resume capability

### Concurrency
- Use `asyncio` + `aiohttp` for async HTTP (not `gql` library — it's synchronous)
- Raw GraphQL POST requests to `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn`
- 20 concurrent workers = ~40 req/s (modest, unlikely to trigger rate limits)
- Add exponential backoff per worker if 429/5xx responses

### Output
```
chunks/
  chunk_01_20251007_20251014.csv.gz  (~1-2 GB compressed)
  chunk_02_20251014_20251021.csv.gz
  ...
  chunk_20_20260218_20260225.csv.gz
  manifest.json   (metadata: worker ranges, row counts, checksums)
```

### Compression
- Gzip each chunk as rows are written (streaming compression)
- Expected compression ratio: ~8-10x
- Total disk: **~7-10 GB** (vs 73GB uncompressed)

## Estimated Times

| Workers | Time |
|---|---|
| Sequential | ~49 hours |
| 5 workers | ~10 hours |
| 10 workers | ~5 hours |
| 20 workers | **~2.5 hours** |

Cloud deployment (lower latency to Goldsky) could cut query times from 0.5s to 0.1-0.2s, potentially halving these further.

## Cloud Deployment: GitHub Codespaces

### Why Codespaces
- Zero setup — Python preinstalled, terminal in browser
- Free tier: 60 hrs/month (plenty for a ~2.5 hour job)
- 32GB disk (enough for ~7-10GB of gzipped chunks)
- Good network (Azure backbone)
- No SSH, no instance management, no teardown

### Workflow
1. Push `scripts/parallel-scrape/` to the repo
2. Open Codespace from GitHub (browser or VS Code)
3. `cd scripts/parallel-scrape && uv run scrape.py`
4. Monitor progress in terminal
5. Download chunks from Codespace file browser, or `git lfs` / `gh` CLI
6. Stop Codespace when done (auto-stops after 30 min idle)

### Disk Budget
- 32GB total on Codespace
- ~7-10GB for gzipped chunks
- ~2GB for Python + deps
- Plenty of headroom

### Getting Data Back
- **Browser download**: right-click files in Codespace file explorer, download
- **gh CLI**: `gh codespace cp remote:chunks/ local:chunks/` from local terminal
- **git push**: only if chunks are small enough (not ideal for 10GB)

## Reassembly
After downloading chunks locally:
```bash
# Option A: Concatenate into single CSV
zcat chunks/chunk_01*.csv.gz > orderFilled_gap.csv
for f in chunks/chunk_0[2-9]*.csv.gz chunks/chunk_[1-2]*.csv.gz; do
    zcat "$f" | tail -n +2 >> orderFilled_gap.csv  # skip header
done

# Option B: Read directly (polars/pandas support .csv.gz)
import polars as pl
df = pl.read_csv("chunks/*.csv.gz")
```

Then append to existing `goldsky/orderFilled.csv` or keep separate.

## Dependencies
- Python 3.10+
- `aiohttp` (async HTTP)
- `asyncio` (stdlib)
- `gzip` (stdlib)
- No `gql` library needed — raw POST requests are simpler and async-compatible

## Resume / Fault Tolerance
- Each worker saves cursor state to `cursors/worker_NN.json`
- On restart, completed chunks are skipped, in-progress chunks resume from cursor
- `manifest.json` tracks which chunks are done
