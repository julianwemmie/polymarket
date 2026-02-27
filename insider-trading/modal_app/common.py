"""Shared Modal configuration: volume, images, constants."""

import modal

vol = modal.Volume.from_name("polymarket-data", create_if_missing=True)

VOL_PATH = "/vol"

# Default gap start: last record in archive (2025-10-07 16:39:50 UTC)
DEFAULT_GAP_START_TS = 1759855190

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

# Gap scraper (async aiohttp workers)
scrape_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("aiohttp>=3.9")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_file("pipeline/scrape/scraper.py", "/app/scraper.py")
)

# Historical download + markets fetcher
fetch_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests>=2.31")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_file("pipeline/scrape/historical.py", "/app/historical.py")
    .add_local_file("pipeline/scrape/markets.py", "/app/markets.py")
)

# Ingest: consolidate + trades processing
# Mounts full pipeline/ so `from pipeline.utils.helpers` imports work
ingest_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("polars>=1.0.0")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_dir("pipeline", remote_path="/app/pipeline")
)

scan_image = modal.Image.debian_slim(python_version="3.12")

# Analysis: signal1 + signal2
analysis_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("polars>=1.0.0")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
)
