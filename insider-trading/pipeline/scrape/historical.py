"""
Download bulk historical OrderFilled events from S3.

Downloads the XZ-compressed CSV from the warproxxx/poly_data archive,
decompresses it, and saves to data/scrape/historical.csv.

Usage:
    uv run python -m pipeline.scrape.historical
"""

import lzma
import os
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "scrape"

DOWNLOAD_URL = "https://polydata-archive.s3.us-east-1.amazonaws.com/orderFilled_complete.csv.xz"
OUTPUT_PATH = OUTPUT_DIR / "historical.csv"

# Streaming buffer size (1 MB)
CHUNK_SIZE = 1024 * 1024


def download_historical(output_path: str = None):
    """Download and decompress historical OrderFilled events."""
    if output_path is None:
        output_path = str(OUTPUT_PATH)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    xz_path = output.with_suffix(".csv.xz")

    # --- Phase 1: Download ---
    print("=" * 60)
    print("Downloading Historical OrderFilled Events")
    print("=" * 60)
    print(f"  URL: {DOWNLOAD_URL}")
    print(f"  XZ:  {xz_path}")
    print(f"  CSV: {output}")
    print()

    resp = requests.get(DOWNLOAD_URL, stream=True, timeout=30)
    resp.raise_for_status()

    total_bytes = int(resp.headers.get("content-length", 0))
    downloaded = 0
    t0 = time.monotonic()

    with open(xz_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            f.write(chunk)
            downloaded += len(chunk)

            elapsed = time.monotonic() - t0
            rate = downloaded / elapsed if elapsed > 0 else 0

            if total_bytes > 0:
                pct = downloaded / total_bytes * 100
                remaining = total_bytes - downloaded
                eta = remaining / rate if rate > 0 else 0
                eta_m, eta_s = divmod(int(eta), 60)
                print(
                    f"\r  Download: {downloaded / (1024**3):.2f}/{total_bytes / (1024**3):.2f} GB "
                    f"({pct:.1f}%) | {rate / (1024**2):.1f} MB/s | ETA {eta_m}m {eta_s:02d}s",
                    end="", flush=True,
                )
            else:
                print(
                    f"\r  Download: {downloaded / (1024**3):.2f} GB | {rate / (1024**2):.1f} MB/s",
                    end="", flush=True,
                )

    dl_elapsed = time.monotonic() - t0
    print(f"\n  Downloaded {downloaded / (1024**3):.2f} GB in {dl_elapsed:.0f}s")

    # --- Phase 2: Decompress ---
    print(f"\nDecompressing XZ → CSV...")
    t0 = time.monotonic()
    decompressed = 0

    with lzma.open(xz_path, "rb") as xz_in, open(output, "wb") as csv_out:
        while True:
            buf = xz_in.read(CHUNK_SIZE)
            if not buf:
                break
            csv_out.write(buf)
            decompressed += len(buf)

            elapsed = time.monotonic() - t0
            rate = decompressed / elapsed if elapsed > 0 else 0
            print(
                f"\r  Decompress: {decompressed / (1024**3):.2f} GB | {rate / (1024**2):.1f} MB/s",
                end="", flush=True,
            )

    decomp_elapsed = time.monotonic() - t0
    print(f"\n  Decompressed {decompressed / (1024**3):.2f} GB in {decomp_elapsed:.0f}s")

    # Clean up XZ file
    xz_path.unlink()
    print(f"\n  Removed {xz_path}")

    print(f"\nDone! Output: {output}")
    print("=" * 60)


if __name__ == "__main__":
    download_historical()
