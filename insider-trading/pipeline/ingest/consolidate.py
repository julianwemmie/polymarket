"""
Consolidate raw OrderFilled events from multiple sources into a single CSV.

Reads the bulk historical CSV and scraper chunks from data/scrape/,
merges them in timestamp order, and writes to data/ingest/orderFilled.csv.

Sources:
    data/scrape/historical.csv      — bulk archive from warproxxx/poly_data
    data/scrape/chunk_*.csv.gz      — gap scraper output (gzipped)

Output:
    data/ingest/orderFilled.csv     — unified events for trades processing

Usage:
    uv run python -m pipeline.ingest.consolidate
"""

import csv
import glob
import gzip
import os
import re
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
SCRAPE_DIR = DATA_ROOT / "scrape"
OUTPUT_DIR = DATA_ROOT / "ingest"

HISTORICAL_PATH = SCRAPE_DIR / "historical.csv"
CHUNKS_GLOB = str(SCRAPE_DIR / "chunk_*.csv.gz")
OUTPUT_PATH = OUTPUT_DIR / "orderFilled.csv"

# Output columns (trades.py expects these)
OUTPUT_COLUMNS = [
    "timestamp", "maker", "makerAssetId", "makerAmountFilled",
    "taker", "takerAssetId", "takerAmountFilled", "transactionHash",
]

# Chunk filename pattern: chunk_W{worker}_{start}_{end}_part{num}.csv.gz
CHUNK_PATTERN = re.compile(r"chunk_(\d+)_(\d{8})_(\d{8})_part(\d+)\.csv\.gz")


def _sorted_chunks() -> list[Path]:
    """Get scraper chunk files sorted by worker ID then part number."""
    chunks = []
    for path in sorted(glob.glob(CHUNKS_GLOB)):
        p = Path(path)
        m = CHUNK_PATTERN.match(p.name)
        if m:
            worker_id = int(m.group(1))
            part_num = int(m.group(4))
            chunks.append((worker_id, part_num, p))

    chunks.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in chunks]


def _stream_csv(path: Path, opener=open):
    """Yield rows from a CSV file, skipping the header."""
    with opener(path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def _stream_gzip_csv(path: Path):
    """Yield rows from a gzipped CSV file, skipping the header."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def consolidate(output_path: str = None):
    """Merge historical data and scraper chunks into a single orderFilled CSV."""
    if output_path is None:
        output_path = str(OUTPUT_PATH)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    has_historical = HISTORICAL_PATH.exists()
    chunk_files = _sorted_chunks()

    print("=" * 60)
    print("Consolidating OrderFilled Events")
    print("=" * 60)
    print(f"  Historical: {HISTORICAL_PATH} {'(found)' if has_historical else '(not found)'}")
    print(f"  Chunks:     {len(chunk_files)} files")
    print(f"  Output:     {output}")

    if not has_historical and not chunk_files:
        print("\nError: No source data found. Run scraping first.")
        return

    sources = []
    if has_historical:
        sources.append(("historical", HISTORICAL_PATH))
    for cf in chunk_files:
        sources.append(("chunk", cf))

    print(f"\n  Sources: {len(sources)} total")
    print()

    total_rows = 0
    t0 = time.monotonic()

    with open(output, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()

        for i, (src_type, src_path) in enumerate(sources, 1):
            source_rows = 0

            if src_type == "historical":
                row_iter = _stream_csv(src_path)
            else:
                row_iter = _stream_gzip_csv(src_path)

            for row in row_iter:
                writer.writerow(row)
                source_rows += 1
                total_rows += 1

            elapsed = time.monotonic() - t0
            rate = total_rows / elapsed if elapsed > 0 else 0
            remaining = len(sources) - i
            avg_per_source = total_rows / i
            eta = remaining * avg_per_source / rate if rate > 0 else 0
            eta_m, eta_s = divmod(int(eta), 60)
            pct = i / len(sources) * 100

            print(
                f"  [{i}/{len(sources)}] {pct:.0f}% | "
                f"{src_path.name}: {source_rows:,} rows | "
                f"Total: {total_rows:,} | {rate:,.0f} rows/s | "
                f"ETA {eta_m}m {eta_s:02d}s"
            )

    elapsed = time.monotonic() - t0
    file_size = output.stat().st_size
    print(f"\nDone! {total_rows:,} rows in {elapsed:.0f}s")
    print(f"Output: {output} ({file_size / (1024**3):.2f} GB)")
    print("=" * 60)


if __name__ == "__main__":
    consolidate()
