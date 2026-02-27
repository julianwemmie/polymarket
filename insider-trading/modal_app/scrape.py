"""
Multi-machine parallel scraper + data fetchers using Modal.

Tasks:
    gap          Scrape recent OrderFilled events from Goldsky (default)
    historical   Download bulk historical data from S3
    markets      Fetch market metadata from Polymarket API
    all          Run all three tasks

Usage:
    modal run modal_app/scrape.py                                    # gap scrape (default)
    modal run modal_app/scrape.py --task historical                  # download bulk archive
    modal run modal_app/scrape.py --task markets                     # fetch market metadata
    modal run modal_app/scrape.py --task all                         # run all three
    modal run modal_app/scrape.py --containers 10                    # 10 x 20 = 200 workers
    modal run modal_app/scrape.py --start 7d                         # scrape last 7 days
    modal run modal_app/scrape.py --start 2026-02-23 --end 2026-02-26
    modal run modal_app/scrape.py --max-batches 5                    # test mode
"""

from __future__ import annotations

from typing import Optional

import modal

from modal_app.common import vol, scrape_image, fetch_image, VOL_PATH, DEFAULT_GAP_START_TS

app = modal.App("polymarket-scraper")


# ---------------------------------------------------------------------------
# Historical download + Markets fetch
# ---------------------------------------------------------------------------


@app.function(
    image=fetch_image,
    volumes={VOL_PATH: vol},
    cpu=2,
    memory=8192,
    timeout=7200,
)
def download_historical(output_base: str = ""):
    """Download and decompress bulk historical OrderFilled data from S3."""
    import os
    import sys
    if output_base:
        os.environ["POLYMARKET_DATA_DIR"] = output_base
    sys.path.insert(0, "/app")
    import historical
    historical.download_historical()
    vol.commit()


@app.function(
    image=fetch_image,
    volumes={VOL_PATH: vol},
    cpu=2,
    memory=4096,
    timeout=3600,
)
def fetch_markets(output_base: str = ""):
    """Fetch market metadata from Polymarket Gamma API."""
    import os
    import sys
    if output_base:
        os.environ["POLYMARKET_DATA_DIR"] = output_base
    sys.path.insert(0, "/app")
    import markets
    markets.fetch_markets()
    vol.commit()


# ---------------------------------------------------------------------------
# Gap scraper functions
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
    """Run density probing on a single container and return the partition plan."""
    import asyncio
    import sys

    import aiohttp

    sys.path.insert(0, "/app")
    import scraper

    scraper.GAP_START_TS = gap_start_ts

    async def _probe():
        sem = asyncio.Semaphore(scraper.MAX_CONCURRENT_REQUESTS)
        timeout = aiohttp.ClientTimeout(total=120, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            partitions, estimated_total = await scraper.estimate_partitions(
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
    timeout=7200,
)
def scrape_partition_group(
    assignments: list[dict],
    max_batches: int | None = None,
    concurrency: int = 8,
    estimated_per_worker: int = 0,
    output_base: str = "",
) -> list[dict]:
    """Scrape assigned partitions concurrently using async workers.

    Each assignment: {"worker_id": int, "start_ts": int, "end_ts": int}
    """
    import asyncio
    import logging
    import os
    import sys
    import time

    import aiohttp

    sys.path.insert(0, "/app")
    from pathlib import Path as _Path
    if output_base:
        os.environ["POLYMARKET_DATA_DIR"] = output_base
    scrape_out = _Path(os.environ.get("POLYMARKET_DATA_DIR", VOL_PATH)) / "scrape"
    os.makedirs(f"{scrape_out}/cursors", exist_ok=True)

    import scraper

    # Send worker log messages to stdout so Modal streams them back
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%H:%M:%S")
    )
    scraper.logger.addHandler(stdout_handler)

    wids = [a["worker_id"] for a in assignments]
    estimated_container = estimated_per_worker * len(assignments)
    print(f"Container starting: {len(assignments)} workers (IDs {wids}), "
          f"concurrency={concurrency}, est. {estimated_container:,} events")

    scraper.progress = scraper.ProgressState(
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
                w.rows for w in scraper.progress.workers.values()
            )
            active = sum(
                1 for w in scraper.progress.workers.values()
                if w.status == "active"
            )
            done = sum(
                1 for w in scraper.progress.workers.values()
                if w.status == "done"
            )
            total_rate = sum(
                w.rate for w in scraper.progress.workers.values()
                if w.status == "active"
            )

            elapsed = time.monotonic() - scraper.progress.start_time
            est = scraper.progress.estimated_total
            pct = (
                (total_rows / est * 100)
                if est > 0
                else 0
            )

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

    async def _scrape():
        sem = asyncio.Semaphore(concurrency)
        timeout = aiohttp.ClientTimeout(total=120, connect=10)

        stop_event = asyncio.Event()
        reporter = asyncio.create_task(_progress_reporter(stop_event))

        # Minimum remaining time range (seconds) worth stealing
        MIN_STEAL_SECS = 300  # 5 minutes

        next_wid = max(a["worker_id"] for a in assignments) + 1
        bounds_map = {}   # worker_id -> WorkerBounds
        pending = {}      # asyncio.Task -> worker_id
        all_results = []

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Launch initial workers with mutable bounds
                for a in assignments:
                    wid = a["worker_id"]
                    b = scraper.WorkerBounds(
                        end_ts=a["end_ts"],
                        original_end_ts=a["end_ts"],
                    )
                    bounds_map[wid] = b
                    task = asyncio.create_task(
                        scraper.worker(
                            session, wid, a["start_ts"], a["end_ts"],
                            resume=False, semaphore=sem,
                            max_batches=max_batches, bounds=b,
                        )
                    )
                    pending[task] = wid

                # Work-stealing loop
                while pending:
                    done, _ = await asyncio.wait(
                        pending.keys(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task in done:
                        wid = pending.pop(task)
                        try:
                            result = task.result()
                            all_results.append(result)
                        except Exception as exc:
                            all_results.append({
                                "worker_id": wid,
                                "status": "failed",
                                "error": str(exc),
                            })

                        if not pending:
                            break

                        # Find active worker with the largest remaining range
                        best_victim = None
                        best_remaining = 0
                        for _, active_wid in pending.items():
                            ws = scraper.progress.workers.get(active_wid)
                            if ws is None or ws.status != "active":
                                continue
                            if ws.current_ts <= 0:
                                continue  # hasn't produced data yet
                            b = bounds_map[active_wid]
                            remaining = b.end_ts - ws.current_ts
                            if remaining > best_remaining:
                                best_remaining = remaining
                                best_victim = active_wid

                        if best_victim is None or best_remaining <= MIN_STEAL_SECS:
                            continue

                        # Split: take the back half of the victim's remaining range
                        victim_bounds = bounds_map[best_victim]
                        victim_ws = scraper.progress.workers[best_victim]
                        midpoint = victim_ws.current_ts + (best_remaining // 2)
                        old_end = victim_bounds.end_ts

                        # Shrink victim's range
                        victim_bounds.end_ts = midpoint

                        # Spawn a new worker for the stolen range
                        steal_wid = next_wid
                        next_wid += 1
                        steal_bounds = scraper.WorkerBounds(
                            end_ts=old_end,
                            original_end_ts=old_end,
                        )
                        bounds_map[steal_wid] = steal_bounds
                        scraper.progress.num_workers += 1
                        scraper.progress.estimated_total += estimated_per_worker

                        print(
                            f"  STEAL: W{steal_wid} taking "
                            f"[{scraper.ts_to_str(midpoint)} -> "
                            f"{scraper.ts_to_str(old_end)}] "
                            f"from W{best_victim:02d} "
                            f"({best_remaining // 60}m remaining)",
                            flush=True,
                        )

                        steal_task = asyncio.create_task(
                            scraper.worker(
                                session, steal_wid, midpoint, old_end,
                                resume=False, semaphore=sem,
                                max_batches=max_batches, bounds=steal_bounds,
                            )
                        )
                        pending[steal_task] = steal_wid

        finally:
            stop_event.set()
            await reporter

        return all_results

    t0 = time.monotonic()
    results = asyncio.run(_scrape())
    elapsed = time.monotonic() - t0

    total_rows = sum(r.get("rows", 0) for r in results)
    failed = sum(1 for r in results if r.get("status") == "failed")
    print(f"Container done: {total_rows:,} rows in {elapsed:.0f}s, "
          f"{failed} failed")

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
    """Verify partition plan has no overlaps or gaps."""
    if not partitions:
        raise ValueError("Empty partition plan")

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

    for i in range(1, len(partitions)):
        prev_end = partitions[i - 1]["end_ts"]
        curr_start = partitions[i]["start_ts"]
        if curr_start != prev_end:
            raise ValueError(
                f"Gap between partition {i-1} (end={prev_end}) "
                f"and partition {i} (start={curr_start})"
            )

    for i, p in enumerate(partitions):
        if p["end_ts"] <= p["start_ts"]:
            raise ValueError(
                f"Partition {i} has zero/negative length: "
                f"{p['start_ts']} -> {p['end_ts']}"
            )

    wids = [p["worker_id"] for p in partitions]
    if len(wids) != len(set(wids)):
        raise ValueError(f"Duplicate worker IDs: {wids}")


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _parse_ts(value: str) -> int:
    """Parse a timestamp string into a unix timestamp.

    Accepts: relative durations ('7d', '24h'), ISO-8601, date only, raw unix ts.
    """
    from datetime import datetime, timezone

    s = value.strip()
    now = int(datetime.now(tz=timezone.utc).timestamp())

    sl = s.lower()
    if sl.endswith("d"):
        return now - int(sl[:-1]) * 86400
    if sl.endswith("h"):
        return now - int(sl[:-1]) * 3600

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue

    return int(s)


# ---------------------------------------------------------------------------
# Local entrypoint
# ---------------------------------------------------------------------------


def _run_gap_scrape(
    containers: int,
    wpc: int,
    start: Optional[str],
    end: Optional[str],
    max_batches: Optional[int],
    output_base: str = "",
):
    """Run multi-machine gap scraping."""
    import math
    import time
    from datetime import datetime, timezone

    total_workers = containers * wpc

    gap_start_ts = DEFAULT_GAP_START_TS
    if start is not None:
        gap_start_ts = _parse_ts(start)

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

    per_container_concurrency = max(wpc + 2, 8)
    total_max_concurrent = containers * per_container_concurrency
    print(f"  Concurrency/container: {per_container_concurrency} "
          f"(total max: {total_max_concurrent})")
    print()

    # Step 1: Probe density
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

    if actual_workers < total_workers:
        total_workers = actual_workers
        containers = math.ceil(actual_workers / wpc)
        print(f"  Adjusted to {containers} containers "
              f"({actual_workers} partitions)")

    # Step 2: Verify & distribute
    print(f"\nStep 2/3: Verifying partition plan...")

    verify_partitions(partitions, gap_start_ts, gap_end_ts)
    print("  Partitions verified: contiguous, no overlaps, no gaps")

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

    # Step 3: Fan out
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
                output_base=output_base,
            )
        )

    all_results = []
    for i, h in enumerate(handles):
        results = h.get()
        all_results.extend(results)
        rows = sum(r.get("rows", 0) for r in results)
        failed = sum(1 for r in results if r.get("status") == "failed")
        status = f", {failed} FAILED" if failed else ""
        print(f"  Container {i} done: {rows:,} rows{status}")

    scrape_time = time.monotonic() - t0

    # Summary
    total_rows = sum(r.get("rows", 0) for r in all_results)
    total_size = sum(r.get("file_size_bytes", 0) for r in all_results)
    errors = [r for r in all_results if r.get("status") == "failed"]
    total_time = probe_time + scrape_time

    print(f"\n{'=' * 60}")
    print("GAP SCRAPE COMPLETE")
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


@app.local_entrypoint()
def main(
    task: str = "gap",
    containers: int = 5,
    wpc: int = 20,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_batches: Optional[int] = None,
    output_dir: Optional[str] = None,
):
    """Orchestrate scraping tasks.

    --task: gap (default), historical, markets, all
    --output-dir: override base output directory on the volume (default: /vol)
    """
    out = output_dir or ""

    if task == "gap":
        _run_gap_scrape(containers, wpc, start, end, max_batches, output_base=out)
    elif task == "historical":
        print("Downloading historical OrderFilled data...")
        download_historical.remote(output_base=out)
        print("Done!")
    elif task == "markets":
        print("Fetching market metadata...")
        fetch_markets.remote(output_base=out)
        print("Done!")
    elif task == "all":
        # Run historical + markets in parallel, then gap scrape
        print("Running all scrape tasks...")
        print()

        h_hist = download_historical.spawn(output_base=out)
        h_markets = fetch_markets.spawn(output_base=out)

        print("Waiting for historical download...")
        h_hist.get()
        print("Historical download complete!")

        print("Waiting for markets fetch...")
        h_markets.get()
        print("Markets fetch complete!")

        print()
        _run_gap_scrape(containers, wpc, start, end, max_batches, output_base=out)
    else:
        raise ValueError(
            f"Unknown task: {task}. Use 'gap', 'historical', 'markets', or 'all'."
        )
