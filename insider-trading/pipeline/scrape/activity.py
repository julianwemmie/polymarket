"""Scrape SPLIT, MERGE, and REDEMPTION events from Polymarket's activity-subgraph.

Supports parallel workers that partition the time range. Each worker writes
its own gzipped CSV chunk per entity type.

Output (single worker):
    data/scrape/splits.csv.gz
    data/scrape/merges.csv.gz
    data/scrape/redemptions.csv.gz

Output (multi-worker):
    data/scrape/splits_chunk_000.csv.gz, splits_chunk_001.csv.gz, ...
    data/scrape/merges_chunk_000.csv.gz, merges_chunk_001.csv.gz, ...
    data/scrape/redemptions_chunk_000.csv.gz, ...

Usage:
    uv run python -m pipeline.scrape.activity                    # 1 worker
    uv run python -m pipeline.scrape.activity --workers 10       # 10 workers
    uv run python -m pipeline.scrape.activity --entity splits    # single entity
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOLDSKY_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/activity-subgraph/0.0.4/gn"
)

BATCH_SIZE = 1000

# Default start: Polymarket CTF exchange launch (June 2020)
DEFAULT_START_TS = 1590969600  # 2020-06-01 00:00:00 UTC

# Retry settings
MAX_RETRIES = 12
BASE_BACKOFF = 1.0
MAX_BACKOFF = 120.0

# Concurrency throttle
MAX_CONCURRENT_REQUESTS = 25

# Number of density probes to estimate event counts across the time range
DENSITY_PROBE_COUNT = 600

# Directories
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = DATA_ROOT / "scrape"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("activity-scrape")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# Entity definitions
# ---------------------------------------------------------------------------

ENTITIES = {
    "splits": {
        "fields": ["id", "timestamp", "stakeholder", "condition", "amount"],
        "query_name": "splits",
    },
    "merges": {
        "fields": ["id", "timestamp", "stakeholder", "condition", "amount"],
        "query_name": "merges",
    },
    "redemptions": {
        "fields": ["id", "timestamp", "redeemer", "condition", "indexSets", "payout"],
        "query_name": "redemptions",
    },
}


def ts_to_str(ts: int) -> str:
    """Convert unix timestamp to human-readable string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Progress state (shared across workers for container-level reporting)
# ---------------------------------------------------------------------------

@dataclass
class WorkerState:
    worker_id: int
    rows: int = 0
    batches: int = 0
    status: str = "pending"  # pending, active, done, failed
    rate: float = 0.0
    start_time: float = 0.0
    current_ts: int = 0


@dataclass
class ProgressState:
    num_workers: int = 0
    workers: dict[int, WorkerState] = field(default_factory=dict)
    estimated_total: int = 0
    start_time: float = 0.0

# Global progress state — set by scrape_partition_group / scrape_partitioned
progress: ProgressState | None = None


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def build_query(
    entity_name: str,
    fields: list[str],
    timestamp_gte: int,
    timestamp_lt: int | None = None,
    first: int = BATCH_SIZE,
) -> str:
    """Build a GraphQL query for a given entity type with optional upper bound."""
    fields_str = " ".join(fields)
    where = f'timestamp_gte: "{timestamp_gte}"'
    if timestamp_lt is not None:
        where += f', timestamp_lt: "{timestamp_lt}"'
    return json.dumps({
        "query": f"""{{
            {entity_name}(
                orderBy: timestamp,
                orderDirection: asc,
                first: {first},
                where: {{{where}}}
            ) {{
                {fields_str}
            }}
        }}"""
    })


async def graphql_post(
    session: aiohttp.ClientSession,
    payload: str,
    label: str = "query",
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Execute a GraphQL POST with exponential backoff on errors."""
    headers = {"Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        try:
            if semaphore is not None:
                await semaphore.acquire()
            try:
                async with session.post(GOLDSKY_URL, data=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "errors" in data:
                            backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                            logger.warning(
                                "[%s] GraphQL error: %s, retrying in %.1fs (%d/%d)",
                                label, str(data["errors"])[:200], backoff, attempt + 1, MAX_RETRIES,
                            )
                            await asyncio.sleep(backoff)
                            continue
                        return data

                    if resp.status in (429, 500, 502, 503, 504):
                        backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                        logger.warning(
                            "[%s] HTTP %d, retrying in %.1fs (%d/%d)",
                            label, resp.status, backoff, attempt + 1, MAX_RETRIES,
                        )
                        await asyncio.sleep(backoff)
                        continue

                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
            finally:
                if semaphore is not None:
                    semaphore.release()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            logger.warning(
                "[%s] Connection error: %s, retrying in %.1fs (%d/%d)",
                label, exc, backoff, attempt + 1, MAX_RETRIES,
            )
            await asyncio.sleep(backoff)

    raise RuntimeError(f"[{label}] Failed after {MAX_RETRIES} retries")


# ---------------------------------------------------------------------------
# Density probing & partitioning
# ---------------------------------------------------------------------------


async def probe_count_at(
    session: aiohttp.ClientSession,
    timestamp: int,
    semaphore: asyncio.Semaphore | None = None,
) -> float:
    """Estimate combined event rate (events/hour) at a timestamp.

    Queries all entity types and sums counts. Uses adaptive window sizing
    (binary search) to find a window that returns ~500 total events, then
    extrapolates the hourly rate.
    """
    TARGET_LOW = 200
    TARGET_HIGH = 800
    MAX_PROBE_ROUNDS = 8

    window_secs = 60
    window_min = 1       # 1 second floor
    window_max = 7200    # 2 hour ceiling
    count = 0

    for _ in range(MAX_PROBE_ROUNDS):
        count = 0
        any_capped = False

        for entity in ENTITIES.values():
            payload = build_query(
                entity["query_name"], entity["fields"],
                timestamp_gte=timestamp,
                timestamp_lt=timestamp + window_secs,
                first=BATCH_SIZE,
            )
            data = await graphql_post(
                session, payload, label="probe", semaphore=semaphore,
            )
            n = len(data.get("data", {}).get(entity["query_name"], []))
            count += n
            if n >= BATCH_SIZE:
                any_capped = True

        if count == 0:
            # No events -- expand aggressively
            window_min = window_secs
            window_secs = min(window_secs * 4, window_max)
            if window_secs >= window_max:
                return 0.0
            continue

        if not any_capped and TARGET_LOW <= count <= TARGET_HIGH:
            return count * (3600.0 / window_secs)

        if any_capped or count > TARGET_HIGH:
            # Window too wide, shrink
            window_max = window_secs
            window_secs = max((window_min + window_secs) // 2, window_min + 1)
        elif count < TARGET_LOW:
            # Too few, expand
            window_min = window_secs
            window_secs = min((window_secs + window_max) // 2, window_max)

    # Exhausted rounds -- best-effort extrapolation
    if count > 0:
        return count * (3600.0 / window_secs)
    return 0.0


async def estimate_partitions(
    session: aiohttp.ClientSession,
    num_workers: int,
    start_ts: int,
    end_ts: int,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[list[tuple[int, int]], int]:
    """Probe density across the time range and create equal-event partitions.

    Returns (partitions, estimated_total) where partitions is a list of
    (start_ts, end_ts) tuples and estimated_total is the approximate event
    count.
    """
    logger.info("Probing event density across time range...")
    logger.info("  Range: %s -> %s", ts_to_str(start_ts), ts_to_str(end_ts))
    logger.info("  Duration: %.1f days", (end_ts - start_ts) / 86400)

    gap_duration = end_ts - start_ts
    probe_interval = gap_duration / DENSITY_PROBE_COUNT

    probe_tasks = []
    probe_timestamps = []
    for i in range(DENSITY_PROBE_COUNT):
        ts = int(start_ts + i * probe_interval)
        probe_timestamps.append(ts)
        probe_tasks.append(probe_count_at(session, ts, semaphore=semaphore))

    probe_results = await asyncio.gather(*probe_tasks)

    # Build cumulative density curve
    densities: list[tuple[int, float]] = []
    for ts, rate in zip(probe_timestamps, probe_results):
        densities.append((ts, rate))

    # Integrate (trapezoid rule) to get cumulative events
    cumulative = [0.0]
    for i in range(1, len(densities)):
        dt_hours = (densities[i][0] - densities[i - 1][0]) / 3600.0
        avg_rate = (densities[i - 1][1] + densities[i][1]) / 2.0
        cumulative.append(cumulative[-1] + avg_rate * dt_hours)

    # Final segment from last probe to end_ts
    final_dt_hours = (end_ts - densities[-1][0]) / 3600.0
    total_estimate = cumulative[-1] + densities[-1][1] * final_dt_hours

    logger.info("  Estimated total events: ~%s", f"{total_estimate:,.0f}")
    events_per_worker = total_estimate / num_workers
    logger.info("  Target events per worker: ~%s", f"{events_per_worker:,.0f}")

    # Walk the cumulative curve to find partition boundaries
    partitions = []
    target_cumulative = 0.0
    partition_start = start_ts

    for _ in range(num_workers - 1):
        target_cumulative += events_per_worker

        partition_end = end_ts  # default
        for i in range(1, len(cumulative)):
            if cumulative[i] >= target_cumulative:
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
    partitions.append((partition_start, end_ts))

    # Merge partitions shorter than 60 seconds (sparse regions)
    MIN_PARTITION_SECS = 60
    merged: list[tuple[int, int]] = []
    for start, end in partitions:
        if merged and (end - start) < MIN_PARTITION_SECS:
            merged[-1] = (merged[-1][0], end)
        elif (end - start) < MIN_PARTITION_SECS and not merged:
            merged.append((start, end))
        else:
            if merged and (merged[-1][1] - merged[-1][0]) < MIN_PARTITION_SECS:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))

    if len(merged) < len(partitions):
        removed = len(partitions) - len(merged)
        logger.warning(
            "Merged %d short partition(s). Worker count reduced from %d to %d.",
            removed, len(partitions), len(merged),
        )
    partitions = merged

    return partitions, int(total_estimate)


# ---------------------------------------------------------------------------
# Worker: scrapes one entity for one time partition
# ---------------------------------------------------------------------------

async def scrape_entity_partition(
    session: aiohttp.ClientSession,
    entity_key: str,
    start_ts: int,
    end_ts: int | None,
    worker_id: int,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Scrape a single entity type for a time range [start_ts, end_ts).

    Returns dict with worker_id, entity, rows, status.
    """
    global progress

    entity = ENTITIES[entity_key]
    query_name = entity["query_name"]
    fields = entity["fields"]

    # Determine output filename
    if worker_id < 0:
        output_path = output_dir / f"{entity_key}.csv.gz"
    else:
        output_path = output_dir / f"{entity_key}_chunk_{worker_id:03d}.csv.gz"

    output_dir.mkdir(parents=True, exist_ok=True)

    cursor_ts = start_ts
    total_rows = 0
    batch_num = 0
    t0 = time.monotonic()
    label = f"W{worker_id:02d}/{entity_key}" if worker_id >= 0 else entity_key

    # Register with progress state
    if progress is not None and worker_id >= 0:
        ws = progress.workers.get(worker_id)
        if ws is None:
            ws = WorkerState(worker_id=worker_id, start_time=t0)
            progress.workers[worker_id] = ws
        ws.status = "active"

    with gzip.open(output_path, "wt", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        while True:
            payload = build_query(query_name, fields, cursor_ts, end_ts)
            data = await graphql_post(session, payload, label=label, semaphore=semaphore)

            records = data.get("data", {}).get(query_name, [])
            if not records:
                break

            batch_num += 1
            for record in records:
                writer.writerow(record)
            total_rows += len(records)

            # Advance cursor
            last_ts = int(records[-1]["timestamp"])
            if last_ts == cursor_ts:
                cursor_ts = last_ts + 1
            else:
                cursor_ts = last_ts

            elapsed = time.monotonic() - t0
            rate = total_rows / elapsed if elapsed > 0 else 0

            # Update progress state
            if progress is not None and worker_id >= 0:
                ws = progress.workers.get(worker_id)
                if ws is not None:
                    ws.rows = total_rows
                    ws.batches = batch_num
                    ws.rate = rate
                    ws.current_ts = cursor_ts

            logger.info(
                "[%s] batch %d | %s rows | %.0f rows/s | cursor %s",
                label, batch_num, f"{total_rows:,}", rate, ts_to_str(cursor_ts),
            )

            if len(records) < BATCH_SIZE:
                break

    elapsed = time.monotonic() - t0

    # Mark worker done in progress state
    if progress is not None and worker_id >= 0:
        ws = progress.workers.get(worker_id)
        if ws is not None:
            ws.status = "done"
            ws.rows = total_rows
            ws.rate = 0.0

    return {
        "worker_id": worker_id,
        "entity": entity_key,
        "rows": total_rows,
        "batches": batch_num,
        "elapsed": elapsed,
        "file": str(output_path),
        "file_size_bytes": output_path.stat().st_size,
        "status": "done",
    }


# ---------------------------------------------------------------------------
# Multi-worker orchestration
# ---------------------------------------------------------------------------

async def scrape_worker(
    session: aiohttp.ClientSession,
    worker_id: int,
    start_ts: int,
    end_ts: int | None,
    entities: list[str],
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Scrape all entity types for a single time partition."""
    results = []
    for entity_key in entities:
        result = await scrape_entity_partition(
            session, entity_key, start_ts, end_ts, worker_id, output_dir, semaphore,
        )
        results.append(result)
    return results


async def _progress_reporter(stop_event: asyncio.Event) -> None:
    """Print container-level progress every 15 seconds."""
    INTERVAL = 15
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=INTERVAL)
            break
        except asyncio.TimeoutError:
            pass

        if progress is None:
            continue

        total_rows = sum(w.rows for w in progress.workers.values())
        active = sum(1 for w in progress.workers.values() if w.status == "active")
        done = sum(1 for w in progress.workers.values() if w.status == "done")
        total_rate = sum(w.rate for w in progress.workers.values() if w.status == "active")

        est = progress.estimated_total
        pct = (total_rows / est * 100) if est > 0 else 0

        if total_rate > 0 and est > total_rows:
            eta_secs = (est - total_rows) / total_rate
            eta_m = int(eta_secs // 60)
            eta_s = int(eta_secs % 60)
            eta_str = f"{eta_m}m {eta_s:02d}s"
        elif active == 0 and done > 0:
            eta_str = "done"
        else:
            eta_str = "..."

        print(
            f"  PROGRESS: {total_rows:,}/{est:,} "
            f"({pct:.1f}%) | {total_rate:,.0f} rows/s | "
            f"{active} active, {done} done | "
            f"ETA {eta_str}",
            flush=True,
        )


async def scrape_partitioned(
    workers: int = 1,
    entities: list[str] | None = None,
    start_ts: int = DEFAULT_START_TS,
    end_ts: int | None = None,
    concurrency: int | None = None,
    estimated_total: int = 0,
) -> list[dict]:
    """Scrape activity events with parallel workers.

    Uses density probing to create equal-event partitions, then runs them
    concurrently.

    Args:
        workers: Number of parallel workers.
        entities: Entity types to scrape. Defaults to all.
        start_ts: Start of time range (unix timestamp). Default: Polymarket launch (June 2020).
        end_ts: End of time range. Default: now.
        concurrency: Max concurrent API requests. Default: MAX_CONCURRENT_REQUESTS.
        estimated_total: Estimated total rows (for progress %). 0 = unknown.

    Returns:
        List of result dicts per (worker, entity) pair.
    """
    global progress

    if entities is None:
        entities = list(ENTITIES.keys())
    if end_ts is None:
        end_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if concurrency is None:
        concurrency = MAX_CONCURRENT_REQUESTS

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=120, connect=10)

    # Probe density and build equal-event partitions
    if workers > 1:
        print("Probing event density...")
        async with aiohttp.ClientSession(timeout=timeout) as probe_session:
            density_partitions, est = await estimate_partitions(
                probe_session, workers, start_ts, end_ts, semaphore=sem,
            )
        if estimated_total == 0:
            estimated_total = est
        partitions = [(i, s, e) for i, (s, e) in enumerate(density_partitions)]
        workers = len(partitions)
    else:
        partitions = [(0, start_ts, end_ts)]

    # Initialize progress state
    progress = ProgressState(
        num_workers=workers,
        estimated_total=estimated_total,
        start_time=time.monotonic(),
    )

    t0 = time.monotonic()

    print("=" * 60)
    print("Activity Scraper (splits / merges / redemptions)")
    print("=" * 60)
    print(f"  Workers:     {workers}")
    print(f"  Entities:    {', '.join(entities)}")
    print(f"  Time range:  {ts_to_str(start_ts)} -> {ts_to_str(end_ts)}")
    print(f"  Concurrency: {concurrency}")
    print(f"  Output:      {output_dir}")
    print()

    for wid, p_start, p_end in partitions:
        days = (p_end - p_start) / 86400
        print(f"    W{wid:02d}: {ts_to_str(p_start)} -> {ts_to_str(p_end)} ({days:.1f}d)")
    print()

    all_results = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        if workers == 1:
            # Single worker: use simple filenames (no chunk suffix)
            for entity_key in entities:
                result = await scrape_entity_partition(
                    session, entity_key, start_ts, end_ts,
                    worker_id=-1, output_dir=output_dir, semaphore=sem,
                )
                all_results.append(result)
                size_mb = result["file_size_bytes"] / (1024 * 1024)
                print(f"  {entity_key}: {result['rows']:,} rows ({size_mb:.1f} MB)")
        else:
            # Multi-worker with progress reporter
            stop_event = asyncio.Event()
            reporter = asyncio.create_task(_progress_reporter(stop_event))

            try:
                tasks = []
                for wid, p_start, p_end in partitions:
                    tasks.append(
                        scrape_worker(session, wid, p_start, p_end, entities, output_dir, sem)
                    )
                worker_results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, wr in enumerate(worker_results):
                    if isinstance(wr, Exception):
                        logger.error("Worker %d failed: %s", i, wr)
                        all_results.append({
                            "worker_id": i, "status": "failed", "error": str(wr),
                        })
                    else:
                        all_results.extend(wr)
            finally:
                stop_event.set()
                await reporter

    elapsed = time.monotonic() - t0

    # Summary
    total_rows = sum(r.get("rows", 0) for r in all_results)
    total_size = sum(r.get("file_size_bytes", 0) for r in all_results)
    failed = sum(1 for r in all_results if r.get("status") == "failed")

    print()
    print("=" * 60)
    print("ACTIVITY SCRAPE COMPLETE")
    print("=" * 60)
    print(f"  Total rows:       {total_rows:,}")
    print(f"  Total compressed: {total_size / (1024**2):.1f} MB")
    print(f"  Time:             {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    if elapsed > 0:
        print(f"  Throughput:       {total_rows / elapsed:,.0f} rows/s")
    print(f"  Workers:          {workers}")
    if failed:
        print(f"  Failed:           {failed}")

    for entity_key in entities:
        entity_rows = sum(
            r.get("rows", 0) for r in all_results
            if r.get("entity") == entity_key
        )
        print(f"  {entity_key:15s} {entity_rows:>10,} rows")

    return all_results


def scrape_activity(
    entities: list[str] | None = None,
    workers: int = 1,
    start_ts: int = DEFAULT_START_TS,
    end_ts: int | None = None,
    concurrency: int | None = None,
) -> list[dict]:
    """Synchronous entry point for scraping activity events."""
    return asyncio.run(scrape_partitioned(
        workers=workers,
        entities=entities,
        start_ts=start_ts,
        end_ts=end_ts,
        concurrency=concurrency,
    ))


# ---------------------------------------------------------------------------
# Container-level entry point (called from Modal)
# ---------------------------------------------------------------------------

def scrape_partition_group(
    assignments: list[dict],
    entities: list[str] | None = None,
    concurrency: int = 8,
    estimated_total: int = 0,
) -> list[dict]:
    """Scrape assigned time partitions concurrently.

    Each assignment: {"worker_id": int, "start_ts": int, "end_ts": int}
    Called by Modal containers to run a group of workers.
    """
    global progress

    if entities is None:
        entities = list(ENTITIES.keys())

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    wids = [a["worker_id"] for a in assignments]
    print(f"Container starting: {len(assignments)} workers (IDs {wids}), "
          f"concurrency={concurrency}, entities={entities}")

    # Initialize progress state for this container
    progress = ProgressState(
        num_workers=len(assignments),
        estimated_total=estimated_total,
        start_time=time.monotonic(),
    )

    async def _run():
        sem = asyncio.Semaphore(concurrency)
        timeout = aiohttp.ClientTimeout(total=120, connect=10)

        stop_event = asyncio.Event()
        reporter = asyncio.create_task(_progress_reporter(stop_event))

        all_results = []
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                tasks = []
                for a in assignments:
                    tasks.append(
                        scrape_worker(
                            session, a["worker_id"], a["start_ts"], a["end_ts"],
                            entities, output_dir, sem,
                        )
                    )
                worker_results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, wr in enumerate(worker_results):
                    if isinstance(wr, Exception):
                        wid = assignments[i]["worker_id"]
                        logger.error("Worker %d failed: %s", wid, wr)
                        all_results.append({
                            "worker_id": wid, "status": "failed", "error": str(wr),
                        })
                    else:
                        all_results.extend(wr)
        finally:
            stop_event.set()
            await reporter

        return all_results

    t0 = time.monotonic()
    results = asyncio.run(_run())
    elapsed = time.monotonic() - t0

    total_rows = sum(r.get("rows", 0) for r in results)
    total_size = sum(r.get("file_size_bytes", 0) for r in results)
    failed = sum(1 for r in results if r.get("status") == "failed")
    print(
        f"Container done: {total_rows:,} rows, "
        f"{total_size / (1024**2):.1f} MB in {elapsed:.0f}s, "
        f"{failed} failed"
    )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape activity events from Polymarket")
    parser.add_argument(
        "--entity",
        choices=list(ENTITIES.keys()),
        help="Scrape a single entity type (default: all)",
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Number of parallel workers (default: 10)",
    )
    args = parser.parse_args()

    target = [args.entity] if args.entity else None
    scrape_activity(entities=target, workers=args.workers)
