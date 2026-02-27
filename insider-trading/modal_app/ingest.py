"""
Modal cloud runner for ingest pipeline (trades processing).

Reads raw scrape data from /vol/scrape/ on the Modal volume,
processes OrderFilled events into structured trades.

Prerequisites (run scrape first):
    modal run modal_app/scrape.py --task all

Run:
    modal run modal_app/ingest.py                          # default output
    modal run modal_app/ingest.py --output-dir /vol/run1   # custom base

Download results:
    modal volume get polymarket-data /ingest/trades/ ./data/ingest/trades/
"""

from typing import Optional

import modal

from modal_app.common import vol, ingest_image, VOL_PATH

app = modal.App("polymarket-ingest")


@app.function(
    image=ingest_image,
    volumes={VOL_PATH: vol},
    cpu=8,
    memory=65536,
    timeout=7200,
)
def process_trades(output_base: str = VOL_PATH):
    """Process raw OrderFilled events into structured trades."""
    import subprocess
    import os

    env = {
        **os.environ,
        "POLYMARKET_DATA_DIR": output_base,
        "PYTHONPATH": "/app",
    }
    result = subprocess.run(["python", "-u", "/app/pipeline/ingest/trades.py"], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"trades processing failed (exit {result.returncode})")
    vol.commit()


@app.local_entrypoint()
def main(output_dir: Optional[str] = None):
    """Run ingest pipeline: process trades from raw scrape data.

    --output-dir: override base output directory on the volume (default: /vol)
    """
    import time

    out = output_dir or VOL_PATH

    print("=" * 60)
    print("Modal Ingest Pipeline")
    print("=" * 60)
    print(f"  Output base: {out}")
    print(f"  Reads from:  {out}/scrape/")
    print(f"  Writes to:   {out}/ingest/")
    t0 = time.monotonic()

    print("\nProcessing trades...")
    process_trades.remote(output_base=out)
    print("  Done!")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    print(f"INGEST COMPLETE ({elapsed:.0f}s)")
    print(f"{'=' * 60}")
    print(f"\nDownload:")
    print(f"  modal volume get polymarket-data {out.removeprefix(VOL_PATH)}/ingest/trades/ ./data/ingest/trades/")
