"""
Modal cloud runner for ingest pipeline (consolidate + trades).

Reads raw data from /vol/scrape/ on the Modal volume, consolidates
OrderFilled events, then processes them into structured trades.

Prerequisites (run scrape first):
    modal run modal_app/scrape.py --task all

Run:
    modal run modal_app/ingest.py                          # default output
    modal run modal_app/ingest.py --output-dir /vol/run1   # custom base

Download results:
    modal volume get polymarket-data /ingest/trades.csv ./data/ingest/trades.csv
"""

from typing import Optional

import modal

from modal_app.common import vol, ingest_image, VOL_PATH

app = modal.App("polymarket-ingest")


def _run_script(script_path: str, output_base: str):
    """Run an ingest script with volume-mounted paths."""
    import subprocess
    import os

    env = {
        **os.environ,
        "POLYMARKET_DATA_DIR": VOL_PATH,
        "POLYMARKET_OUTPUT_DIR": output_base,
        "PYTHONPATH": "/app",
    }
    result = subprocess.run(["python", script_path], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{script_path} failed (exit {result.returncode})")
    vol.commit()


# ---------------------------------------------------------------------------
# Consolidate: merge historical + chunks → orderFilled.csv
# ---------------------------------------------------------------------------

@app.function(
    image=ingest_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=8192,
    timeout=7200,
)
def consolidate(output_base: str = VOL_PATH):
    """Merge historical data and scraper chunks into a single CSV."""
    _run_script("/app/pipeline/ingest/consolidate.py", output_base)


# ---------------------------------------------------------------------------
# Trades: process orderFilled + markets → trades.csv
# ---------------------------------------------------------------------------

@app.function(
    image=ingest_image,
    volumes={VOL_PATH: vol},
    cpu=8,
    memory=65536,
    timeout=7200,
)
def process_trades(output_base: str = VOL_PATH):
    """Process raw OrderFilled events into structured trades."""
    _run_script("/app/pipeline/ingest/trades.py", output_base)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(output_dir: Optional[str] = None):
    """Run full ingest pipeline: consolidate → trades.

    --output-dir: override base output directory on the volume (default: /vol)
    """
    import time

    out = output_dir or VOL_PATH

    print("=" * 60)
    print("Modal Ingest Pipeline")
    print("=" * 60)
    print(f"  Output base: {out}")
    print(f"  Writes to:   {out}/ingest/")
    t0 = time.monotonic()

    print("\nStep 1/2: Consolidating OrderFilled events...")
    consolidate.remote(output_base=out)
    print("  Consolidation complete!")

    print("\nStep 2/2: Processing trades...")
    process_trades.remote(output_base=out)
    print("  Trades processing complete!")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    print(f"INGEST COMPLETE ({elapsed:.0f}s)")
    print(f"{'=' * 60}")
    print(f"\nDownload results:")
    print(f"  modal volume get polymarket-data {out.removeprefix(VOL_PATH)}/ingest/trades.csv ./data/ingest/trades.csv")
