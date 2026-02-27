"""
Multi-machine parallel Goldsky scraper using Modal.

Distributes scrape.py's partitioned workers across N Modal containers
for horizontal speedup. Each container runs M async workers, all writing
to a shared Modal Volume with uniquely-named chunk files (no conflicts).

Partition boundaries guarantee no overlap and no gaps:
  - Worker K covers [boundary_K, boundary_K+1) using timestamp_gte/timestamp_lt
  - Adjacent workers share exact boundaries
  - Verified before launch with contiguity checks

Usage:
    modal run modal_scrape.py                                    # 5 containers x 20 workers = 100 total
    modal run modal_scrape.py --containers 10                    # 10 x 20 = 200 total
    modal run modal_scrape.py --containers 3 --wpc 10            # 3 x 10 = 30 total
    modal run modal_scrape.py --start 7d                         # scrape last 7 days
    modal run modal_scrape.py --start 2026-02-23 --end 2026-02-26  # exact date range
    modal run modal_scrape.py --start 2026-02-23T09:08:04          # ISO datetime start
    modal run modal_scrape.py --max-batches 5                    # test mode: 5 batches per worker

Download results:
    modal volume get polymarket-data /scrape/ ./data/scrape/
"""

from __future__ import annotations

from typing import Optional

import modal

app = modal.App("polymarket-scraper")
vol = modal.Volume.from_name("polymarket-data", create_if_missing=True)

scrape_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("aiohttp>=3.9")
    .env({"POLYMARKET_DATA_DIR": "/vol"})
    .add_local_file("scrape.py", "/app/scrape.py")
)

VOL_PATH = "/vol"

# Default gap start: last record in archive (2025-10-07 16:39:50 UTC)
DEFAULT_GAP_START_TS = 1759855190


# ---------------------------------------------------------------------------
# Modal functions
# ---------------------------------------------------------------------------


@app.function(
    image=scrape_image,
    volumes={VOL_PATH: vol},
    cpu=2,
    memory=4096,
    timeout=3600,
)
def probe_density(
    total_workers: int,
    gap_start_ts: int,
    gap_end_ts: int,
) -> dict:
    """Run density probing on a single container and return the partition plan.

    Returns dict with keys: partitions, estimated_total, actual_workers.
    """
    import asyncio
    import sys

    import aiohttp

    sys.path.insert(0, "/app")
    import scrape

    scrape.GAP_START_TS = gap_start_ts

    async def _probe():
        sem = asyncio.Semaphore(scrape.MAX_CONCURRENT_REQUESTS)
        timeout = aiohttp.ClientTimeout(total=120, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            partitions, estimated_total = await scrape.estimate_partitions(
                session, total_workers, gap_end_ts, semaphore=sem
            )
            return {
                "partitions": [
                    {"worker_id": i, "start_ts": s, "end_ts": e}
                    for i, (s, e) in enumerate(partitions)
                ],
                "estimated_total": estimated_total,
                "actual_workers": len(partitions),
            }

    return asyncio.run(_probe())


@app.function(
    image=scrape_image,
    volumes={VOL_PATH: vol},
    cpu=2,
    memory=4096,
    timeout=7200,  # 2 hours
)
def scrape_partition_group(
    assignments: list[dict],
    max_batches: int | None = None,
    concurrency: int = 8,
    estimated_per_worker: int = 0,
) -> list[dict]:
    """Scrape assigned partitions concurrently using async workers.

    Each assignment: {"worker_id": int, "start_ts": int, "end_ts": int}
    concurrency: max in-flight API requests for this container.
    estimated_per_worker: estimated events per worker (for progress reporting).
    """
    import asyncio
    import os
    import sys
    import time

    import aiohttp

    sys.path.insert(0, "/app")

    # Ensure output dirs exist on volume before importing scrape
    # (scrape module creates dirs too, but be safe)
    os.makedirs("/vol/scrape/cursors", exist_ok=True)

    import logging

    import scrape

    # Send worker log messages to stdout so Modal streams them back
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%H:%M:%S")
    )
    scrape.logger.addHandler(stdout_handler)

    wids = [a["worker_id"] for a in assignments]
    estimated_container = estimated_per_worker * len(assignments)
    print(f"Container starting: {len(assignments)} workers (IDs {wids}), "
          f"concurrency={concurrency}, est. {estimated_container:,} events")

    # Initialize scrape.progress so workers update their state
    scrape.progress = scrape.ProgressState(
        num_workers=len(assignments),
        start_time=time.monotonic(),
        estimated_total=estimated_container,
        phase="scraping",
    )

    async def _progress_reporter(stop_event: asyncio.Event):
        """Print container-level progress every 15 seconds."""
        INTERVAL = 15
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=INTERVAL)
                break
            except asyncio.TimeoutError:
                pass

            total_rows = sum(
                w.rows for w in scrape.progress.workers.values()
            )
            active = sum(
                1 for w in scrape.progress.workers.values()
                if w.status == "active"
            )
            done = sum(
                1 for w in scrape.progress.workers.values()
                if w.status == "done"
            )
            total_rate = sum(
                w.rate for w in scrape.progress.workers.values()
                if w.status == "active"
            )

            elapsed = time.monotonic() - scrape.progress.start_time
            pct = (
                (total_rows / estimated_container * 100)
                if estimated_container > 0
                else 0
            )

            if total_rate > 0 and estimated_container > total_rows:
                eta_secs = (estimated_container - total_rows) / total_rate
                eta_m = int(eta_secs // 60)
                eta_s = int(eta_secs % 60)
                eta_str = f"{eta_m}m {eta_s:02d}s"
            elif done == len(assignments):
                eta_str = "done"
            else:
                eta_str = "..."

            print(
                f"  PROGRESS: {total_rows:,}/{estimated_container:,} "
                f"({pct:.1f}%) | {total_rate:,.0f} rows/s | "
                f"{active} active, {done} done | "
                f"ETA {eta_str}",
                flush=True,
            )

    async def _scrape():
        sem = asyncio.Semaphore(concurrency)
        timeout = aiohttp.ClientTimeout(total=120, connect=10)

        stop_event = asyncio.Event()
        reporter = asyncio.create_task(_progress_reporter(stop_event))

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                tasks = [
                    scrape.worker(
                        session,
                        a["worker_id"],
                        a["start_ts"],
                        a["end_ts"],
                        resume=False,
                        semaphore=sem,
                        max_batches=max_batches,
                    )
                    for a in assignments
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            stop_event.set()
            await reporter

        outputs = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                outputs.append({
                    "worker_id": assignments[i]["worker_id"],
                    "status": "failed",
                    "error": str(r),
                })
            else:
                outputs.append(r)
        return outputs

    t0 = time.monotonic()
    results = asyncio.run(_scrape())
    elapsed = time.monotonic() - t0

    total_rows = sum(r.get("rows", 0) for r in results)
    failed = sum(1 for r in results if r.get("status") == "failed")
    print(f"Container done: {total_rows:,} rows in {elapsed:.0f}s, "
          f"{failed} failed")

    # Persist chunks to shared volume
    vol.commit()

    return results


# ---------------------------------------------------------------------------
# Partition verification (runs locally)
# ---------------------------------------------------------------------------


def verify_partitions(
    partitions: list[dict],
    gap_start_ts: int,
    gap_end_ts: int,
) -> None:
    """Verify partition plan has no overlaps or gaps.

    Raises ValueError if any issue is found.
    """
    if not partitions:
        raise ValueError("Empty partition plan")

    # Check first and last boundaries
    if partitions[0]["start_ts"] != gap_start_ts:
        raise ValueError(
            f"First partition starts at {partitions[0]['start_ts']} "
            f"but gap starts at {gap_start_ts}"
        )
    if partitions[-1]["end_ts"] != gap_end_ts:
        raise ValueError(
            f"Last partition ends at {partitions[-1]['end_ts']} "
            f"but gap ends at {gap_end_ts}"
        )

    # Check contiguity: each partition's start == previous partition's end
    for i in range(1, len(partitions)):
        prev_end = partitions[i - 1]["end_ts"]
        curr_start = partitions[i]["start_ts"]
        if curr_start != prev_end:
            raise ValueError(
                f"Gap between partition {i-1} (end={prev_end}) "
                f"and partition {i} (start={curr_start})"
            )

    # Check no zero-length partitions
    for i, p in enumerate(partitions):
        if p["end_ts"] <= p["start_ts"]:
            raise ValueError(
                f"Partition {i} has zero/negative length: "
                f"{p['start_ts']} -> {p['end_ts']}"
            )

    # Check unique worker IDs
    wids = [p["worker_id"] for p in partitions]
    if len(wids) != len(set(wids)):
        raise ValueError(f"Duplicate worker IDs: {wids}")


# ---------------------------------------------------------------------------
# Local entrypoint
# ---------------------------------------------------------------------------


def _parse_ts(value: str) -> int:
    """Parse a timestamp string into a unix timestamp.

    Accepts:
        - Relative durations: '7d', '24h'
        - ISO-8601 datetime: '2026-02-23T09:08:04'
        - Date only: '2026-02-23'
        - Raw unix timestamp: '1759855190'
    """
    from datetime import datetime, timezone

    s = value.strip()
    now = int(datetime.now(tz=timezone.utc).timestamp())

    # Relative durations
    sl = s.lower()
    if sl.endswith("d"):
        return now - int(sl[:-1]) * 86400
    if sl.endswith("h"):
        return now - int(sl[:-1]) * 3600

    # ISO-8601 datetime (with or without seconds)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue

    # Raw unix timestamp
    return int(s)


@app.local_entrypoint()
def main(
    containers: int = 5,
    wpc: int = 20,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_batches: Optional[int] = None,
):
    """Orchestrate multi-machine scraping.

    Args:
        containers: Number of Modal containers to run in parallel.
        wpc: Workers per container (async workers within each container).
        start: Start timestamp — unix ts, relative ('7d', '24h'), ISO date/datetime.
        end: End timestamp — same formats as start. Defaults to now.
        max_batches: Stop each worker after N batches (for testing).
    """
    import math
    import time
    from datetime import datetime, timezone

    total_workers = containers * wpc

    # Parse start timestamp
    gap_start_ts = DEFAULT_GAP_START_TS
    if start is not None:
        gap_start_ts = _parse_ts(start)

    # Parse end timestamp
    if end is not None:
        gap_end_ts = _parse_ts(end)
    else:
        gap_end_ts = int(datetime.now(tz=timezone.utc).timestamp())

    gap_start_str = datetime.fromtimestamp(
        gap_start_ts, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    gap_end_str = datetime.fromtimestamp(
        gap_end_ts, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    gap_days = (gap_end_ts - gap_start_ts) / 86400

    print("=" * 60)
    print("Modal Multi-Machine Goldsky Scraper")
    print("=" * 60)
    print(f"  Containers:         {containers}")
    print(f"  Workers/container:  {wpc}")
    print(f"  Total workers:      {total_workers}")
    print(f"  Gap start:          {gap_start_str}")
    print(f"  Gap end:            {gap_end_str}")
    print(f"  Gap duration:       {gap_days:.1f} days")
    if max_batches is not None:
        print(f"  Max batches/worker: {max_batches}")
    print()

    # Per-container concurrency: limit API requests so we don't overwhelm
    # Goldsky across all containers. Total max = containers * per_container.
    per_container_concurrency = max(wpc + 2, 8)
    total_max_concurrent = containers * per_container_concurrency
    print(f"  Concurrency/container: {per_container_concurrency} "
          f"(total max: {total_max_concurrent})")
    print()

    # ------------------------------------------------------------------
    # Step 1: Probe density
    # ------------------------------------------------------------------
    print(f"Step 1/3: Probing event density ({total_workers} partitions)...")
    t0 = time.monotonic()

    plan = probe_density.remote(total_workers, gap_start_ts, gap_end_ts)

    partitions = plan["partitions"]
    actual_workers = plan["actual_workers"]
    estimated_total = plan["estimated_total"]

    probe_time = time.monotonic() - t0
    print(f"  Done in {probe_time:.0f}s")
    print(f"  Partitions:   {actual_workers}")
    print(f"  Est. events:  {estimated_total:,}")

    # Recompute containers if partition merging reduced worker count
    if actual_workers < total_workers:
        total_workers = actual_workers
        containers = math.ceil(actual_workers / wpc)
        print(f"  Adjusted to {containers} containers "
              f"({actual_workers} partitions)")

    # ------------------------------------------------------------------
    # Step 2: Verify & distribute
    # ------------------------------------------------------------------
    print(f"\nStep 2/3: Verifying partition plan...")

    verify_partitions(partitions, gap_start_ts, gap_end_ts)
    print("  Partitions verified: contiguous, no overlaps, no gaps")

    # Print partition summary
    for p in partitions:
        start_str = datetime.fromtimestamp(
            p["start_ts"], tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")
        end_str = datetime.fromtimestamp(
            p["end_ts"], tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")
        days = (p["end_ts"] - p["start_ts"]) / 86400
        print(f"    W{p['worker_id']:02d}: {start_str} -> {end_str} "
              f"({days:.1f}d)")

    # ------------------------------------------------------------------
    # Step 3: Fan out across containers
    # ------------------------------------------------------------------
    estimated_per_worker = (
        estimated_total // actual_workers if actual_workers > 0 else 0
    )

    print(f"\nStep 3/3: Launching {containers} containers...")
    print(f"  Est. events/worker: {estimated_per_worker:,}")
    t0 = time.monotonic()

    handles = []
    for c in range(containers):
        chunk_start = c * wpc
        chunk_end = min(chunk_start + wpc, actual_workers)
        if chunk_start >= actual_workers:
            break
        batch = partitions[chunk_start:chunk_end]
        wids = [p["worker_id"] for p in batch]
        print(f"  Container {c}: workers {wids}")
        handles.append(
            scrape_partition_group.spawn(
                batch,
                max_batches=max_batches,
                concurrency=per_container_concurrency,
                estimated_per_worker=estimated_per_worker,
            )
        )

    # Collect results as containers finish
    all_results = []
    for i, h in enumerate(handles):
        results = h.get()
        all_results.extend(results)
        rows = sum(r.get("rows", 0) for r in results)
        failed = sum(1 for r in results if r.get("status") == "failed")
        status = f", {failed} FAILED" if failed else ""
        print(f"  Container {i} done: {rows:,} rows{status}")

    scrape_time = time.monotonic() - t0

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_rows = sum(r.get("rows", 0) for r in all_results)
    total_size = sum(r.get("file_size_bytes", 0) for r in all_results)
    errors = [r for r in all_results if r.get("status") == "failed"]
    total_time = probe_time + scrape_time

    print(f"\n{'=' * 60}")
    print("SCRAPE COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Total rows:       {total_rows:,}")
    print(f"  Total compressed: {total_size / (1024**3):.2f} GB")
    print(f"  Probe time:       {probe_time:.0f}s")
    print(f"  Scrape time:      {scrape_time:.0f}s")
    print(f"  Total time:       {total_time:.0f}s "
          f"({total_time / 60:.1f} min)")
    if total_time > 0:
        print(f"  Throughput:       {total_rows / scrape_time:,.0f} rows/s")
    print(f"  Containers:       {len(handles)}")
    print(f"  Workers:          {actual_workers}")

    if errors:
        print(f"\n  ERRORS: {len(errors)}")
        for e in errors:
            print(f"    Worker {e['worker_id']}: {e.get('error', 'unknown')}")

    print(f"\nDownload results:")
    print(f"  modal volume get polymarket-data /scrape/ ./data/scrape/")
