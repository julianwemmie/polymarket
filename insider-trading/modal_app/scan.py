"""
Scan Modal volume for scraped chunks and identify gaps.

Reads chunk files directly on Modal (no download needed), reports
coverage, gaps, and optionally row counts.

Usage:
    modal run modal_app/scan.py                    # quick: filenames only (~5s)
    modal run modal_app/scan.py --full             # full: parallel decompress + row counts
"""

from __future__ import annotations

import modal

from modal_app.common import vol, scan_image, VOL_PATH

app = modal.App("polymarket-scanner")

SCRAPE_DIR = f"{VOL_PATH}/scrape"


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
    """Filename-only scan. No decompression."""
    import json
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

    for cf in chunk_files:
        m = pattern.match(cf.name)
        if not m:
            continue
        wid = int(m.group(1))
        workers[wid]["parts"] += 1
        workers[wid]["file_size"] += cf.stat().st_size

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

    # Load manifest
    manifest_path = scrape_dir / "manifest.json"
    expected_partitions = None
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        expected_partitions = manifest.get("partitions", [])

    def ts_str(ts):
        if ts == float("inf") or ts == 0 or ts is None:
            return "N/A"
        return datetime.fromtimestamp(
            ts, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

    missing_wids = _gap_analysis(all_worker_ids, expected_partitions, ts_str)

    all_names = [cf.name for cf in chunk_files]

    return {
        "total_rows": None,
        "total_size_bytes": total_size,
        "num_workers": len(all_worker_ids),
        "worker_ids": all_worker_ids,
        "missing_workers": missing_wids,
        "chunk_names": all_names,
        "mode": "quick",
    }


# ---------------------------------------------------------------------------
# Gap analysis (shared)
# ---------------------------------------------------------------------------


def _gap_analysis(
    all_worker_ids: list[int],
    expected_partitions: list[dict] | None,
    ts_str,
) -> list[int]:
    """Shared gap analysis logic."""
    missing_wids = []

    print(f"\n{'=' * 60}")
    print("GAP ANALYSIS")
    print(f"{'=' * 60}")

    if expected_partitions:
        expected_wids = {p["worker_id"] for p in expected_partitions}
        present_wids = set(all_worker_ids)
        missing_wids = sorted(expected_wids - present_wids)

        print(f"  Expected: {len(expected_wids)} workers")
        print(f"  Present:  {len(present_wids)} workers")
        print(f"  Missing:  {len(missing_wids)} workers")

        if not missing_wids:
            print("\n  No gaps found! All expected workers have data.")
        else:
            print(f"\n  MISSING workers ({len(missing_wids)}):")
            for wid in missing_wids:
                p = next(
                    p for p in expected_partitions
                    if p["worker_id"] == wid
                )
                print(
                    f"    W{wid:03d}: {ts_str(p['start_ts'])} -> "
                    f"{ts_str(p['end_ts'])}"
                )

            gaps = []
            for wid in missing_wids:
                p = next(
                    p for p in expected_partitions
                    if p["worker_id"] == wid
                )
                gaps.append((p["start_ts"], p["end_ts"]))

            gaps.sort()
            merged = [gaps[0]]
            for start, end in gaps[1:]:
                if start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            print(f"\n  CONTIGUOUS GAPS ({len(merged)}):")
            for start, end in merged:
                days = (end - start) / 86400
                print(
                    f"    {ts_str(start)} -> {ts_str(end)} "
                    f"({days:.1f}d, ts {start}-{end})"
                )

            print(f"\n  RE-SCRAPE COMMANDS:")
            for start, end in merged:
                days = (end - start) / 86400
                est_workers = max(2, int(days * 5))
                est_containers = max(1, est_workers // 20)
                print(
                    f"    modal run modal_app/scrape.py "
                    f"--start {start} --containers {est_containers} "
                    f"--wpc {min(est_workers, 20)}"
                )
    else:
        if all_worker_ids:
            max_wid = max(all_worker_ids)
            missing_wids = sorted(
                set(range(max_wid + 1)) - set(all_worker_ids)
            )
            if missing_wids:
                print(f"  Missing worker IDs (no manifest): {missing_wids}")
            else:
                print("  No gaps in worker ID sequence.")
                print("  (No manifest found -- can't verify time ranges)")

    return missing_wids


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
        print(f"  Missing:       {len(result.get('missing_workers', []))}")
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

    def ts_str(ts):
        if ts == float("inf") or ts == 0 or ts is None:
            return "N/A"
        return datetime.fromtimestamp(
            ts, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

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
            f" {ts_str(w['min_ts']):>22} {ts_str(w['max_ts']):>22} "
            f"{w['parts']:>5}"
        )
        total_rows += w["total_rows"]
        total_size += w["file_size"]

    print("-" * 86)
    print(
        f"  TOTAL  {total_rows:>12,} {total_size / (1024**2):>9.1f} "
        f" {ts_str(global_min):>22} {ts_str(global_max):>22} "
        f"{sum(w['parts'] for w in workers.values()):>5}"
    )

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total rows:    {total_rows:,}")
    print(f"  Total size:    {total_size / (1024**3):.2f} GB")
    print(f"  Workers found: {len(all_worker_ids)}")
    print(f"  Time range:    {ts_str(global_min)} -> {ts_str(global_max)}")
    print(f"  Errors:        {errors}")
