"""
Parallel Goldsky orderFilledEvents scraper.

Splits the gap period into equal-event partitions and scrapes each partition
concurrently using asyncio + aiohttp. Each worker writes to its own gzipped
CSV chunk with streaming compression and cursor-based resume.

Usage:
    uv run python scrape.py                 # 20 workers, fresh start
    uv run python scrape.py --workers 10    # 10 workers
    uv run python scrape.py --resume        # resume from saved cursor state
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOLDSKY_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)

BATCH_SIZE = 1000  # hard limit from Goldsky

# Gap boundaries
GAP_START_TS = 1728259200   # 2025-10-07 00:00:00 UTC
GAP_END_TS = int(datetime.now(tz=timezone.utc).timestamp())

# Number of density probes to estimate event counts across the gap
DENSITY_PROBE_COUNT = 40

# CSV columns (matching existing schema)
CSV_COLUMNS = [
    "timestamp",
    "maker",
    "makerAssetId",
    "makerAmountFilled",
    "taker",
    "takerAssetId",
    "takerAmountFilled",
    "transactionHash",
]

# Retry settings
MAX_RETRIES = 12
BASE_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 120.0  # seconds

# Directories (relative to script location)
SCRIPT_DIR = Path(__file__).resolve().parent
CHUNKS_DIR = SCRIPT_DIR / "chunks"
CURSORS_DIR = SCRIPT_DIR / "cursors"
MANIFEST_PATH = SCRIPT_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def build_query(where_clause: str, first: int = BATCH_SIZE) -> str:
    """Build a GraphQL query string for orderFilledEvents."""
    return json.dumps({
        "query": f"""{{
            orderFilledEvents(
                orderBy: timestamp,
                orderDirection: asc,
                first: {first},
                where: {{{where_clause}}}
            ) {{
                id
                timestamp
                maker
                makerAssetId
                makerAmountFilled
                taker
                takerAssetId
                takerAmountFilled
                transactionHash
            }}
        }}"""
    })


async def graphql_post(
    session: aiohttp.ClientSession,
    payload: str,
    worker_id: str = "probe",
) -> dict:
    """Execute a GraphQL POST with exponential backoff on errors."""
    headers = {"Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(
                GOLDSKY_URL, data=payload, headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "errors" in data:
                        raise RuntimeError(f"GraphQL errors: {data['errors']}")
                    return data

                if resp.status in (429, 500, 502, 503, 504):
                    backoff = min(
                        BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF
                    )
                    text = await resp.text()
                    print(
                        f"  [{worker_id}] HTTP {resp.status}, "
                        f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    await asyncio.sleep(backoff)
                    continue

                text = await resp.text()
                raise RuntimeError(
                    f"HTTP {resp.status}: {text[:200]}"
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            print(
                f"  [{worker_id}] Connection error: {exc}, "
                f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})"
            )
            await asyncio.sleep(backoff)

    raise RuntimeError(f"[{worker_id}] Failed after {MAX_RETRIES} retries")


# ---------------------------------------------------------------------------
# Density probing & partitioning
# ---------------------------------------------------------------------------

async def probe_count_at(
    session: aiohttp.ClientSession,
    timestamp: int,
) -> int:
    """Get the approximate event count up to a timestamp by fetching a small
    batch and checking the ID ordering. Since Goldsky doesn't have a count
    query, we estimate density by measuring how many events exist in a fixed
    time window starting at the given timestamp."""
    payload = build_query(
        f'timestamp_gte: "{timestamp}", timestamp_lt: "{timestamp + 3600}"',
        first=BATCH_SIZE,
    )
    data = await graphql_post(session, payload, worker_id="probe")
    events = data.get("data", {}).get("orderFilledEvents", [])
    return len(events)


async def estimate_partitions(
    session: aiohttp.ClientSession,
    num_workers: int,
) -> list[tuple[int, int]]:
    """Probe density across the gap and create equal-event partitions.

    Returns a list of (start_ts, end_ts) tuples, one per worker.
    """
    print(f"\nProbing event density across gap period...")
    print(f"  Gap: {ts_to_str(GAP_START_TS)} -> {ts_to_str(GAP_END_TS)}")
    print(f"  Duration: {(GAP_END_TS - GAP_START_TS) / 86400:.1f} days")

    # Probe density at evenly-spaced points across the gap
    gap_duration = GAP_END_TS - GAP_START_TS
    probe_interval = gap_duration / DENSITY_PROBE_COUNT

    probe_tasks = []
    probe_timestamps = []
    for i in range(DENSITY_PROBE_COUNT):
        ts = int(GAP_START_TS + i * probe_interval)
        probe_timestamps.append(ts)
        probe_tasks.append(probe_count_at(session, ts))

    probe_results = await asyncio.gather(*probe_tasks)

    # Build a cumulative density curve
    # Each probe gives us events/hour at that point; integrate to get total
    densities = []  # (timestamp, events_per_hour)
    for ts, count in zip(probe_timestamps, probe_results):
        events_per_hour = count  # count in 1-hour window, capped at 1000
        densities.append((ts, events_per_hour))
        date_str = ts_to_str(ts)
        print(f"  {date_str}: ~{events_per_hour} events/hour")

    # Integrate to get approximate cumulative events at each probe point
    # Since probes are capped at 1000, extrapolate for high-density periods
    cumulative = [0.0]
    for i in range(1, len(densities)):
        dt_hours = (densities[i][0] - densities[i - 1][0]) / 3600.0
        avg_rate = (densities[i - 1][1] + densities[i][1]) / 2.0
        cumulative.append(cumulative[-1] + avg_rate * dt_hours)

    # Add the final segment from last probe to GAP_END_TS
    final_dt_hours = (GAP_END_TS - densities[-1][0]) / 3600.0
    total_estimate = cumulative[-1] + densities[-1][1] * final_dt_hours

    print(f"\n  Estimated total events in gap: ~{total_estimate:,.0f}")
    events_per_worker = total_estimate / num_workers
    print(f"  Target events per worker: ~{events_per_worker:,.0f}")

    # Find partition boundaries by walking the cumulative curve
    partitions = []
    target_cumulative = 0.0
    partition_start = GAP_START_TS

    for w in range(num_workers - 1):
        target_cumulative += events_per_worker

        # Find the timestamp where cumulative events reaches target
        partition_end = GAP_END_TS  # default
        for i in range(1, len(cumulative)):
            if cumulative[i] >= target_cumulative:
                # Linear interpolation between probe points
                frac = (
                    (target_cumulative - cumulative[i - 1])
                    / (cumulative[i] - cumulative[i - 1])
                    if cumulative[i] != cumulative[i - 1]
                    else 0.5
                )
                partition_end = int(
                    densities[i - 1][0]
                    + frac * (densities[i][0] - densities[i - 1][0])
                )
                break

        partitions.append((partition_start, partition_end))
        partition_start = partition_end

    # Last partition goes to the end
    partitions.append((partition_start, GAP_END_TS))

    print(f"\nPartition plan ({num_workers} workers):")
    for i, (start, end) in enumerate(partitions):
        duration_days = (end - start) / 86400
        print(
            f"  Worker {i:02d}: {ts_to_str(start)} -> {ts_to_str(end)} "
            f"({duration_days:.1f} days)"
        )

    return partitions


# ---------------------------------------------------------------------------
# Cursor state management
# ---------------------------------------------------------------------------

def cursor_path(worker_id: int) -> Path:
    return CURSORS_DIR / f"worker_{worker_id:02d}.json"


def load_cursor(worker_id: int) -> dict | None:
    """Load saved cursor state for a worker."""
    path = cursor_path(worker_id)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_cursor_state(
    worker_id: int,
    last_timestamp: int,
    last_id: str | None,
    sticky_timestamp: int | None,
    rows_written: int,
) -> None:
    """Save cursor state for a worker."""
    CURSORS_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "last_timestamp": last_timestamp,
        "last_id": last_id,
        "sticky_timestamp": sticky_timestamp,
        "rows_written": rows_written,
    }
    with open(cursor_path(worker_id), "w") as f:
        json.dump(state, f)


def clear_cursor(worker_id: int) -> None:
    """Remove cursor file for a completed worker."""
    path = cursor_path(worker_id)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    """Load or create the manifest."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"workers": {}, "created": datetime.now(tz=timezone.utc).isoformat()}


def save_manifest(manifest: dict) -> None:
    """Save the manifest."""
    manifest["updated"] = datetime.now(tz=timezone.utc).isoformat()
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def ts_to_str(ts: int) -> str:
    """Convert a Unix timestamp to a human-readable UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def ts_to_date(ts: int) -> str:
    """Convert a Unix timestamp to YYYYMMDD."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")


def file_md5(path: Path) -> str:
    """Compute MD5 checksum of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def worker(
    session: aiohttp.ClientSession,
    worker_id: int,
    start_ts: int,
    end_ts: int,
    resume: bool,
) -> dict:
    """Scrape a single time partition and write to a gzipped CSV chunk.

    Returns metadata dict for the manifest.
    """
    chunk_name = f"chunk_{worker_id:02d}_{ts_to_date(start_ts)}_{ts_to_date(end_ts)}.csv.gz"
    chunk_path = CHUNKS_DIR / chunk_name
    wid = f"W{worker_id:02d}"

    # Resume from cursor if available
    last_timestamp = start_ts
    last_id: str | None = None
    sticky_timestamp: int | None = None
    rows_written = 0
    append_mode = False
    # Track whether we've moved past the initial partition start.
    # On the very first batch of a fresh start we use timestamp_gte to include
    # the partition boundary. After the first batch we switch to timestamp_gt.
    past_start = False

    if resume:
        cursor = load_cursor(worker_id)
        if cursor:
            last_timestamp = cursor["last_timestamp"]
            last_id = cursor.get("last_id")
            sticky_timestamp = cursor.get("sticky_timestamp")
            rows_written = cursor.get("rows_written", 0)
            append_mode = True
            past_start = True  # cursor means we already consumed some data
            print(
                f"  [{wid}] Resuming from timestamp {last_timestamp} "
                f"({ts_to_str(last_timestamp)}), {rows_written} rows already written"
            )

    # Check if chunk is already complete (exists in manifest)
    manifest = load_manifest()
    worker_key = str(worker_id)
    if worker_key in manifest.get("workers", {}):
        info = manifest["workers"][worker_key]
        if info.get("status") == "complete":
            print(f"  [{wid}] Already complete ({info.get('rows', 0)} rows), skipping")
            return info

    print(
        f"  [{wid}] Starting: {ts_to_str(start_ts)} -> {ts_to_str(end_ts)} "
        f"({(end_ts - start_ts) / 86400:.1f} days)"
    )

    # Open gzip file for streaming writes
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    if append_mode and chunk_path.exists():
        # For resume, we read existing data, then rewrite + append
        # This is simpler than seeking in a gzip stream
        existing_data = b""
        try:
            with gzip.open(chunk_path, "rb") as f:
                existing_data = f.read()
        except Exception:
            existing_data = b""
            rows_written = 0
            append_mode = False

        gz = gzip.open(chunk_path, "wb", compresslevel=6)
        if existing_data:
            gz.write(existing_data)
        else:
            # Write header
            header_line = ",".join(CSV_COLUMNS) + "\n"
            gz.write(header_line.encode())
    else:
        gz = gzip.open(chunk_path, "wb", compresslevel=6)
        header_line = ",".join(CSV_COLUMNS) + "\n"
        gz.write(header_line.encode())

    batch_count = 0
    t0 = time.monotonic()

    try:
        while True:
            # Build where clause
            if sticky_timestamp is not None:
                # Paginating within a single timestamp by ID
                where = f'timestamp: "{sticky_timestamp}", id_gt: "{last_id}"'
            elif past_start:
                # We already consumed last_timestamp, skip past it
                where = (
                    f'timestamp_gt: "{last_timestamp}", '
                    f'timestamp_lt: "{end_ts}"'
                )
            else:
                # Very first batch: include the partition start boundary
                where = (
                    f'timestamp_gte: "{last_timestamp}", '
                    f'timestamp_lt: "{end_ts}"'
                )

            payload = build_query(where)
            data = await graphql_post(session, payload, worker_id=wid)

            events = data.get("data", {}).get("orderFilledEvents", [])

            if not events:
                if sticky_timestamp is not None:
                    # Done with this sticky timestamp, move on
                    last_timestamp = sticky_timestamp
                    sticky_timestamp = None
                    last_id = None
                    continue
                # No more data in this partition
                break

            # Sort by timestamp, then id
            events.sort(key=lambda e: (int(e["timestamp"]), e["id"]))

            batch_first_ts = int(events[0]["timestamp"])
            batch_last_ts = int(events[-1]["timestamp"])
            batch_last_id = events[-1]["id"]

            # Write rows to gzip stream
            for event in events:
                row = ",".join(str(event.get(col, "")) for col in CSV_COLUMNS)
                gz.write((row + "\n").encode())
                rows_written += 1

            # Determine sticky timestamp handling
            if len(events) >= BATCH_SIZE:
                if batch_first_ts == batch_last_ts:
                    # All 1000 results have the same timestamp -- must paginate by ID
                    sticky_timestamp = batch_last_ts
                    last_id = batch_last_id
                else:
                    # Full batch spanning multiple timestamps --
                    # set sticky to the last timestamp to ensure we don't miss
                    # events at that exact second
                    sticky_timestamp = batch_last_ts
                    last_id = batch_last_id
            else:
                # Partial batch -- this timestamp is complete
                if sticky_timestamp is not None:
                    last_timestamp = sticky_timestamp
                    sticky_timestamp = None
                    last_id = None
                else:
                    last_timestamp = batch_last_ts

            batch_count += 1
            past_start = True  # after first batch, always use timestamp_gt

            # Save cursor state every batch
            save_cursor_state(
                worker_id, last_timestamp, last_id, sticky_timestamp, rows_written
            )

            # Progress logging every 50 batches
            if batch_count % 50 == 0:
                elapsed = time.monotonic() - t0
                rate = rows_written / elapsed if elapsed > 0 else 0
                print(
                    f"  [{wid}] Batch {batch_count}: "
                    f"ts {batch_last_ts} ({ts_to_str(batch_last_ts)}), "
                    f"{rows_written:,} rows, {rate:,.0f} rows/s"
                )

    finally:
        gz.close()

    elapsed = time.monotonic() - t0
    rate = rows_written / elapsed if elapsed > 0 else 0

    # Compute checksum
    checksum = file_md5(chunk_path)
    file_size = chunk_path.stat().st_size

    # Clear cursor on completion
    clear_cursor(worker_id)

    result = {
        "worker_id": worker_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_date": ts_to_str(start_ts),
        "end_date": ts_to_str(end_ts),
        "chunk_file": chunk_name,
        "rows": rows_written,
        "batches": batch_count,
        "elapsed_seconds": round(elapsed, 1),
        "rows_per_second": round(rate, 1),
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "md5": checksum,
        "status": "complete",
    }

    print(
        f"  [{wid}] DONE: {rows_written:,} rows in {batch_count} batches, "
        f"{elapsed:.1f}s ({rate:,.0f} rows/s), "
        f"{result['file_size_mb']:.1f} MB compressed"
    )

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(num_workers: int, resume: bool) -> None:
    print("=" * 70)
    print("Parallel Goldsky orderFilledEvents Scraper")
    print("=" * 70)
    print(f"  Workers:    {num_workers}")
    print(f"  Resume:     {resume}")
    print(f"  Gap start:  {ts_to_str(GAP_START_TS)} (ts={GAP_START_TS})")
    print(f"  Gap end:    {ts_to_str(GAP_END_TS)} (ts={GAP_END_TS})")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Output:     {CHUNKS_DIR}/")
    print(f"  Goldsky:    {GOLDSKY_URL}")
    print()

    # Create output directories
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    CURSORS_DIR.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        # If resuming with an existing manifest that has partitions, reuse them
        partitions = None
        if resume:
            manifest = load_manifest()
            if "partitions" in manifest and len(manifest["partitions"]) == num_workers:
                partitions = [
                    (p["start_ts"], p["end_ts"]) for p in manifest["partitions"]
                ]
                print("Reusing partition plan from previous run.")

        if partitions is None:
            partitions = await estimate_partitions(session, num_workers)

        # Save partition plan to manifest
        manifest = load_manifest()
        manifest["partitions"] = [
            {"worker_id": i, "start_ts": s, "end_ts": e}
            for i, (s, e) in enumerate(partitions)
        ]
        manifest["num_workers"] = num_workers
        manifest["gap_start_ts"] = GAP_START_TS
        manifest["gap_end_ts"] = GAP_END_TS
        save_manifest(manifest)

        # Launch all workers concurrently
        print(f"\nLaunching {num_workers} workers...\n")
        t0 = time.monotonic()

        tasks = [
            worker(session, i, start, end, resume)
            for i, (start, end) in enumerate(partitions)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    total_rows = 0
    total_size = 0
    errors = []

    manifest = load_manifest()
    if "workers" not in manifest:
        manifest["workers"] = {}

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append((i, str(result)))
            print(f"\n  [W{i:02d}] FAILED: {result}")
        else:
            manifest["workers"][str(i)] = result
            total_rows += result["rows"]
            total_size += result["file_size_bytes"]

    save_manifest(manifest)

    elapsed = time.monotonic() - t0

    # Summary
    print("\n" + "=" * 70)
    print("SCRAPE COMPLETE")
    print("=" * 70)
    print(f"  Total rows:       {total_rows:,}")
    print(f"  Total compressed: {total_size / (1024**3):.2f} GB")
    print(f"  Total time:       {elapsed / 60:.1f} minutes")
    print(f"  Avg throughput:   {total_rows / elapsed:,.0f} rows/s")
    print(f"  Workers:          {num_workers}")
    if errors:
        print(f"  Errors:           {len(errors)}")
        for wid, err in errors:
            print(f"    Worker {wid}: {err}")
    print(f"  Manifest:         {MANIFEST_PATH}")
    print(f"  Chunks:           {CHUNKS_DIR}/")
    print()


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Parallel Goldsky orderFilledEvents scraper"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Number of concurrent workers (default: 20)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from saved cursor state",
    )
    args = parser.parse_args()

    if args.workers < 1:
        print("Error: --workers must be >= 1")
        sys.exit(1)

    asyncio.run(main(args.workers, args.resume))


if __name__ == "__main__":
    cli()
