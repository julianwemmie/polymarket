"""Shared Modal configuration: volume, images, constants."""

import modal

vol = modal.Volume.from_name("polymarket-data", create_if_missing=True)

VOL_PATH = "/vol"

# Default gap start: last record in archive (2025-10-07 16:39:50 UTC)
DEFAULT_GAP_START_TS = 1759855190

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

# Modal requires add_local_* calls after build steps (pip_install etc.),
# so each image adds modal_app/ last for `from modal_app.common import …`.
_base = modal.Image.debian_slim(python_version="3.12")

def _with_modal_app(image: modal.Image) -> modal.Image:
    """Append modal_app/ package so remote containers can resolve imports."""
    return image.add_local_dir("modal_app", remote_path="/root/modal_app")

# Gap scraper (async aiohttp workers)
scrape_image = _with_modal_app(
    _base
    .pip_install("aiohttp>=3.9")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_file("pipeline/scrape/scraper.py", "/app/scraper.py")
)

# Historical download + markets fetcher
fetch_image = _with_modal_app(
    _base
    .pip_install("requests>=2.31")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_file("pipeline/scrape/historical.py", "/app/historical.py")
    .add_local_file("pipeline/scrape/markets.py", "/app/markets.py")
)

# Ingest: trades processing (reads directly from scrape sources)
# Mounts full pipeline/ so `from pipeline.utils.helpers` imports work
ingest_image = _with_modal_app(
    _base
    .pip_install("polars>=1.0.0")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_dir("pipeline", remote_path="/app/pipeline")
)

scan_image = _with_modal_app(_base)

# Analysis: signal1 + signal2
analysis_image = _with_modal_app(
    _base
    .pip_install("polars>=1.0.0")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
)

# Explore: on-demand analysis with both signal pipelines + DuckDB
explore_image = _with_modal_app(
    _base
    .pip_install("polars>=1.0.0", "duckdb>=1.1", "pyarrow>=14.0")
    .env({"POLYMARKET_DATA_DIR": VOL_PATH})
    .add_local_dir("pipeline/analyze/signal1", remote_path="/app/pipeline/analyze/signal1")
    .add_local_dir("pipeline/analyze/signal2", remote_path="/app/pipeline/analyze/signal2")
)
