"""Shared Modal configuration: volume, images, constants."""

import modal

vol = modal.Volume.from_name("polymarket-data", create_if_missing=True)

VOL_PATH = "/vol"

# Default gap start: last record in archive (2025-10-07 16:39:50 UTC)
DEFAULT_GAP_START_TS = 1759855190

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

scrape_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("aiohttp>=3.9")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_file("pipeline/scrape/scraper.py", "/app/scraper.py")
)

scan_image = modal.Image.debian_slim(python_version="3.12")

analysis_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("polars>=1.0.0")
)
