"""
Scan Modal volume for scraped chunks and identify time gaps.

Uses time-based analysis (not manifest-based) to find missing time slots.
Quick mode extracts approximate coverage from filenames; full mode reads
actual timestamps from CSV data.

Usage:
    modal run modal_app/scan.py                    # quick: filenames only (~5s)
    modal run modal_app/scan.py --full             # full: parallel decompress + row counts
"""

from __future__ import annotations

import modal

from modal_app.common import vol, scan_image, VOL_PATH, DEFAULT_GAP_START_TS

app = modal.App("polymarket-scanner")

SCRAPE_DIR = f"{VOL_PATH}/scrape"

# Gaps shorter than this (seconds) are ignored to avoid false positives
# from partition boundary rounding
GAP_TOLERANCE_SECS = 60


# ---------------------------------------------------------------------------
# Per-batch scanner (runs on its own Modal container)
# ---------------------------------------------------------------------------


@app.function(
    image=scan_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=4096,
    timeout=1800,
)
def scan_batch(chunk_names: list[str]) -> list[dict]:
    """Scan a batch of chunk files on one container. Returns per-chunk metadata."""
    import csv
    import gzip
    import re
    import time as _time_mod
    from pathlib import Path

    pattern = re.compile(
        r"chunk_(\d+)_(\d{8})_(\d{8})_part(\d+)\.csv\.gz"
    )
    _t0 = _time_mod.monotonic()

    scrape_dir = Path(SCRAPE_DIR)
    results = []

    for name in chunk_names:
        chunk_path = scrape_dir / name
        m = pattern.match(name)
        if not m:
            results.append({"file": name, "skipped": True})
            continue

        worker_id = int(m.group(1))
        part_num = int(m.group(4))
        file_size = chunk_path.stat().st_size

        rows = 0
        chunk_min_ts = float("inf")
        chunk_max_ts = 0

        try:
            with gzip.open(chunk_path, "rt", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows += 1
                    ts = int(row["timestamp"])
                    if ts < chunk_min_ts:
                        chunk_min_ts = ts
                    if ts > chunk_max_ts:
                        chunk_max_ts = ts
        except Exception as e:
            results.append({
                "file": name,
                "worker_id": worker_id,
                "part_num": part_num,
                "error": str(e),
            })
            continue

        results.append({
            "file": name,
            "worker_id": worker_id,
            "part_num": part_num,
            "rows": rows,
            "file_size": file_size,
            "min_ts": chunk_min_ts if chunk_min_ts != float("inf") else 0,
            "max_ts": chunk_max_ts,
        })

        done = len(results)
        total = len(chunk_names)
        elapsed = _time_mod.monotonic() - _t0
        rate = done / elapsed if elapsed > 0 else 0
        remaining = total - done
        eta = int(remaining / rate) if rate > 0 else 0
        pct = done / total * 100
        total_rows = sum(r.get("rows", 0) for r in results)
        print(
            f"  [{done}/{total}] {pct:.0f}% | "
            f"{total_rows:,} rows | "
            f"{rate:.1f} chunks/s | ETA {eta}s",
            flush=True,
        )

    print(f"  DONE: {len(chunk_names)} chunks, "
          f"{sum(r.get('rows', 0) for r in results):,} rows", flush=True)
    return results


# ---------------------------------------------------------------------------
# Quick scan (single container, no decompression)
# ---------------------------------------------------------------------------


@app.function(
    image=scan_image,
    volumes={VOL_PATH: vol},
    cpu=2,
    memory=4096,
    timeout=600,
)
def scan_quick() -> dict:
    """Filename-only scan with approximate time coverage from filenames."""
    import re
    from collections import defaultdict
    from datetime import datetime, timezone
    from pathlib import Path

    scrape_dir = Path(SCRAPE_DIR)
    if not scrape_dir.exists():
        return {"error": "No scrape directory found on volume"}

    chunk_files = sorted(scrape_dir.glob("chunk_*.csv.gz"))
    if not chunk_files:
        return {"error": "No chunk files found in /scrape/"}

    total_chunks = len(chunk_files)
    total_bytes = sum(f.stat().st_size for f in chunk_files)

    pattern = re.compile(
        r"chunk_(\d+)_(\d{8})_(\d{8})_part(\d+)\.csv\.gz"
    )

    print(f"QUICK SCAN: {total_chunks} chunk files "
          f"({total_bytes / (1024**3):.2f} GB compressed)\n", flush=True)

    workers = defaultdict(lambda: {"parts": 0, "file_size": 0})
    # Collect (start_date, end_date) intervals from filenames
    intervals = []

    for cf in chunk_files:
        m = pattern.match(cf.name)
        if not m:
            continue
        wid = int(m.group(1))
        date_start_str = m.group(2)  # YYYYMMDD
        date_end_str = m.group(3)    # YYYYMMDD
        workers[wid]["parts"] += 1
        workers[wid]["file_size"] += cf.stat().st_size

        # Convert YYYYMMDD to unix timestamps (start of day UTC)
        start_dt = datetime.strptime(date_start_str, "%Y%m%d").replace(
            tzinfo=timezone.utc
        )
        # End date: use end-of-day (23:59:59) since the filename date is
        # the partition end date and events run through that day
        end_dt = datetime.strptime(date_end_str, "%Y%m%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        intervals.append((int(start_dt.timestamp()), int(end_dt.timestamp())))

    all_worker_ids = sorted(workers.keys())

    print(f"{'Worker':>8} {'Size MB':>10} {'Parts':>6}")
    print("-" * 30)

    total_size = 0
    for wid in all_worker_ids:
        w = workers[wid]
        size_mb = w["file_size"] / (1024 * 1024)
        print(f"  W{wid:03d}  {size_mb:>9.1f}  {w['parts']:>5}")
        total_size += w["file_size"]

    print("-" * 30)
    print(f"  TOTAL  {total_size / (1024**2):>9.1f}  "
          f"{sum(w['parts'] for w in workers.values()):>5}")

    # Time-based gap analysis from filename dates (approximate, day-level)
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    gaps = _find_time_gaps(intervals, DEFAULT_GAP_START_TS, now_ts)
    _print_time_gap_report(gaps, DEFAULT_GAP_START_TS, now_ts, approximate=True)

    all_names = [cf.name for cf in chunk_files]

    return {
        "total_rows": None,
        "total_size_bytes": total_size,
        "num_workers": len(all_worker_ids),
        "worker_ids": all_worker_ids,
        "gaps": gaps,
        "chunk_names": all_names,
        "mode": "quick",
    }


# ---------------------------------------------------------------------------
# Time-based gap analysis
# ---------------------------------------------------------------------------


def _merge_intervals(
    intervals: list[tuple[int, int]],
    tolerance: int = GAP_TOLERANCE_SECS,
) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent time intervals.

    Intervals within `tolerance` seconds of each other are merged.
    """
    if not intervals:
        return []
    sorted_ivs = sorted(intervals)
    merged = [sorted_ivs[0]]
    for start, end in sorted_ivs[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + tolerance:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _find_time_gaps(
    intervals: list[tuple[int, int]],
    expected_start: int,
    expected_end: int,
) -> list[tuple[int, int]]:
    """Find uncovered time slots between expected_start and expected_end.

    Returns list of (gap_start, gap_end) tuples for missing time ranges.
    """
    if not intervals:
        return [(expected_start, expected_end)]

    covered = _merge_intervals(intervals)

    gaps = []

    # Gap before first covered interval
    if covered[0][0] > expected_start + GAP_TOLERANCE_SECS:
        gaps.append((expected_start, covered[0][0]))

    # Gaps between covered intervals
    for i in range(1, len(covered)):
        gap_start = covered[i - 1][1]
        gap_end = covered[i][0]
        if gap_end - gap_start > GAP_TOLERANCE_SECS:
            gaps.append((gap_start, gap_end))

    # Gap after last covered interval
    if covered[-1][1] < expected_end - GAP_TOLERANCE_SECS:
        gaps.append((covered[-1][1], expected_end))

    return gaps


def _fmt_duration(secs: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    if secs < 86400:
        return f"{secs / 3600:.1f}h"
    return f"{secs / 86400:.1f}d"


def _ts_str(ts: int) -> str:
    """Convert unix timestamp to human-readable UTC string."""
    from datetime import datetime, timezone

    if ts == float("inf") or ts == 0 or ts is None:
        return "N/A"
    return datetime.fromtimestamp(
        ts, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


def _print_time_gap_report(
    gaps: list[tuple[int, int]],
    expected_start: int,
    expected_end: int,
    approximate: bool = False,
) -> None:
    """Print a time-based gap analysis report."""
    total_span = expected_end - expected_start
    total_gap = sum(end - start for start, end in gaps)
    covered = total_span - total_gap
    pct = (covered / total_span * 100) if total_span > 0 else 0

    label = " (approximate, from filenames)" if approximate else ""

    print(f"\n{'=' * 60}")
    print(f"TIME COVERAGE{label}")
    print(f"{'=' * 60}")
    print(f"  Expected: {_ts_str(expected_start)} -> {_ts_str(expected_end)} "
          f"({_fmt_duration(total_span)})")
    print(f"  Coverage: {pct:.1f}%")

    if not gaps:
        print("\n  No gaps found! Full time range is covered.")
    else:
        print(f"\n  MISSING TIME SLOTS ({len(gaps)}):")
        for start, end in gaps:
            duration = end - start
            print(
                f"    {_ts_str(start)} -> {_ts_str(end)} "
                f"({_fmt_duration(duration)}, ts {start}-{end})"
            )

        print(f"\n  RE-SCRAPE COMMANDS:")
        for start, end in gaps:
            duration = end - start
            days = duration / 86400
            est_workers = max(2, int(days * 5))
            est_containers = max(1, est_workers // 20)
            print(
                f"    modal run modal_app/scrape.py "
                f"--start {start} --end {end} "
                f"--containers {est_containers} "
                f"--wpc {min(est_workers, 20)}"
            )


# ---------------------------------------------------------------------------
# Local entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(full: bool = False):
    """Scan volume for chunk coverage and gaps."""
    import time
    from collections import defaultdict
    from datetime import datetime, timezone

    if not full:
        print("Scanning Modal volume (quick mode)...\n")
        result = scan_quick.remote()

        if "error" in result:
            print(f"Error: {result['error']}")
            return

        print(f"\n{'=' * 60}")
        print("SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total size:    {result['total_size_bytes'] / (1024**3):.2f} GB")
        print(f"  Workers found: {result['num_workers']}")
        gaps = result.get("gaps", [])
        print(f"  Time gaps:     {len(gaps)}")
        if gaps:
            print("  (Run with --full for precise timestamp-level gap analysis)")
        return

    # Full mode
    print("Scanning Modal volume (full mode)...")
    print("Step 1: Listing chunks...\n")

    quick_result = scan_quick.remote()
    if "error" in quick_result:
        print(f"Error: {quick_result['error']}")
        return

    chunk_names = quick_result["chunk_names"]
    total_chunks = len(chunk_names)

    CHUNKS_PER_CONTAINER = 15
    batches = []
    for i in range(0, total_chunks, CHUNKS_PER_CONTAINER):
        batches.append(chunk_names[i:i + CHUNKS_PER_CONTAINER])

    num_containers = len(batches)
    print(f"\nStep 2: Scanning {total_chunks} chunks across "
          f"{num_containers} containers "
          f"({CHUNKS_PER_CONTAINER} chunks each)...")
    t0 = time.monotonic()

    handles = [scan_batch.spawn(batch) for batch in batches]

    all_chunk_results = []
    for i, h in enumerate(handles):
        batch_results = h.get()
        all_chunk_results.extend(batch_results)
        elapsed = time.monotonic() - t0
        done = i + 1
        rate = done / elapsed if elapsed > 0 else 0
        remaining = num_containers - done
        eta = remaining / rate if rate > 0 else 0
        print(
            f"  [{done}/{num_containers}] containers done | "
            f"{len(all_chunk_results):,} chunks scanned | "
            f"ETA {int(eta)}s",
            flush=True,
        )

    elapsed = time.monotonic() - t0
    print(f"\nScan complete in {elapsed:.0f}s\n")

    # Aggregate per-worker stats
    workers = defaultdict(lambda: {
        "total_rows": 0,
        "min_ts": float("inf"),
        "max_ts": 0,
        "file_size": 0,
        "parts": 0,
    })

    errors = 0
    for r in all_chunk_results:
        if r.get("skipped"):
            continue
        if r.get("error"):
            errors += 1
            print(f"  ERROR: {r['file']}: {r['error']}")
            continue

        wid = r["worker_id"]
        w = workers[wid]
        w["total_rows"] += r["rows"]
        w["file_size"] += r["file_size"]
        w["parts"] += 1
        if r["min_ts"] < w["min_ts"]:
            w["min_ts"] = r["min_ts"]
        if r["max_ts"] > w["max_ts"]:
            w["max_ts"] = r["max_ts"]

    all_worker_ids = sorted(workers.keys())
    workers_with_data = [w for w in workers.values() if w["total_rows"] > 0]

    if not workers_with_data:
        print("Error: All chunks are empty or errored")
        return

    global_min = min(w["min_ts"] for w in workers_with_data)
    global_max = max(w["max_ts"] for w in workers_with_data)

    # Per-worker table
    print(f"{'Worker':>8} {'Rows':>12} {'Size MB':>10} "
          f"{'Min TS':>22} {'Max TS':>22} {'Parts':>6}")
    print("-" * 86)

    total_rows = 0
    total_size = 0
    for wid in all_worker_ids:
        w = workers[wid]
        size_mb = w["file_size"] / (1024 * 1024)
        print(
            f"  W{wid:03d}  {w['total_rows']:>12,} {size_mb:>9.1f} "
            f" {_ts_str(w['min_ts']):>22} {_ts_str(w['max_ts']):>22} "
            f"{w['parts']:>5}"
        )
        total_rows += w["total_rows"]
        total_size += w["file_size"]

    print("-" * 86)
    print(
        f"  TOTAL  {total_rows:>12,} {total_size / (1024**2):>9.1f} "
        f" {_ts_str(global_min):>22} {_ts_str(global_max):>22} "
        f"{sum(w['parts'] for w in workers.values()):>5}"
    )

    # Time-based gap analysis from actual timestamps
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    intervals = [
        (w["min_ts"], w["max_ts"])
        for w in workers.values()
        if w["total_rows"] > 0
    ]
    gaps = _find_time_gaps(intervals, DEFAULT_GAP_START_TS, now_ts)
    _print_time_gap_report(gaps, DEFAULT_GAP_START_TS, now_ts, approximate=False)

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total rows:    {total_rows:,}")
    print(f"  Total size:    {total_size / (1024**3):.2f} GB")
    print(f"  Workers found: {len(all_worker_ids)}")
    print(f"  Time range:    {_ts_str(global_min)} -> {_ts_str(global_max)}")
    print(f"  Time gaps:     {len(gaps)}")
    print(f"  Errors:        {errors}")
