#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["psutil>=5.9.0"]
# ///
"""
Run all signal1 (implausibility) analysis scripts.

Order:
  1. build_wallet_positions  (reads trades.csv -> wallet_positions.parquet)
     Skipped if wallet_positions.parquet already exists. Use --rebuild to force.
  2. 8 metric scripts sequentially (each loads wallet_positions.parquet)
  3. aggregate_score (reads all 8 metric outputs)

Memory safety:
  - Monitors system memory while each subprocess runs.
  - Warns at 85%, kills at 93% (macOS handles pressure well for single processes).
  - Waits between scripts for memory to settle.

Usage:
  cd pipeline/analyze/signal1
  uv run run_all.py            # skip build if output exists
  uv run run_all.py --rebuild  # force rebuild from trades.csv
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(DATA_ROOT / "analyze" / "signal1")))

BUILD = SCRIPT_DIR / "build_positions.py"
BUILD_OUTPUT = OUTPUT_DIR / "wallet_positions.parquet"

# Each metric script writes to output/<name>.parquet
METRICS = [
    ("roi.py",                      "roi.parquet"),
    ("brier_score.py",              "brier_score.parquet"),
    ("contrarian_win_rate.py",      "contrarian_win_rate.parquet"),
    ("niche_market_accuracy.py",    "niche_market_accuracy.parquet"),
    ("position_concentration.py",   "position_concentration.parquet"),
    ("profit_factor.py",            "profit_factor.parquet"),
    ("bet_size_vs_odds.py",         "bet_size_vs_odds.parquet"),
    ("win_streak.py",               "win_streak.parquet"),
]
AGGREGATE = SCRIPT_DIR / "aggregate.py"

MEM_WARN_PERCENT = 85
MEM_KILL_PERCENT = 93
MEM_CHECK_INTERVAL = 2  # seconds
MEM_SETTLE_PERCENT = 65  # wait for memory to drop below this between scripts


def check_memory() -> tuple[float, float]:
    """Return (percent_used, gb_available)."""
    vm = psutil.virtual_memory()
    return vm.percent, vm.available / (1024 ** 3)


def run_script_with_mem_guard(path: Path) -> tuple[str, float, bool]:
    """Run a script, streaming stdout live. Monitor memory and kill if over limit."""
    name = path.stem
    t0 = time.time()
    killed = False
    warned = False

    proc = subprocess.Popen(
        ["uv", "run", str(path)],
        stdout=sys.stdout,
        stderr=sys.stdout,
    )

    def monitor():
        nonlocal killed, warned
        while proc.poll() is None:
            pct, avail_gb = check_memory()
            if pct > MEM_KILL_PERCENT:
                killed = True
                print(
                    f"\n  !! KILLING {name}: memory at {pct:.0f}% "
                    f"({avail_gb:.1f} GB free)",
                    flush=True,
                )
                proc.kill()
                return
            if pct > MEM_WARN_PERCENT and not warned:
                warned = True
                print(
                    f"\n  ! WARNING: memory at {pct:.0f}% ({avail_gb:.1f} GB free)",
                    flush=True,
                )
            time.sleep(MEM_CHECK_INTERVAL)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()

    proc.wait()
    watcher.join(timeout=1)

    elapsed = time.time() - t0
    success = proc.returncode == 0 and not killed

    return name, elapsed, success


def main():
    rebuild = "--rebuild" in sys.argv
    t_total = time.time()

    pct, avail_gb = check_memory()
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    print("Signal 1: Implausibility Analysis")
    print(f"RAM: {total_gb:.0f} GB total, {avail_gb:.1f} GB free ({pct:.0f}% used)")
    print(f"Memory: warn at {MEM_WARN_PERCENT}%, kill at {MEM_KILL_PERCENT}%", flush=True)

    # --- Step 1: Build wallet positions ---
    print(f"\n{'='*60}")
    print("  Step 1/3: build_wallet_positions")
    print(f"{'='*60}", flush=True)

    if BUILD_OUTPUT.exists() and not rebuild:
        size_gb = BUILD_OUTPUT.stat().st_size / 1e9
        print(f"  [SKIP] wallet_positions.parquet exists ({size_gb:.1f} GB)")
        print(f"         Use --rebuild to force regeneration.")
    else:
        name, elapsed, success = run_script_with_mem_guard(BUILD)
        print(f"\n  [{'OK' if success else 'FAILED'}] {name} ({elapsed:.1f}s)", flush=True)
        if not success:
            sys.exit(1)

    # --- Step 2: Metric scripts (sequential) ---
    total_metrics = len(METRICS)
    for i, (script_name, output_name) in enumerate(METRICS, 1):
        script_path = SCRIPT_DIR / script_name
        output_path = OUTPUT_DIR / output_name

        # Record mtime before running so we can detect if it was written
        old_mtime = output_path.stat().st_mtime if output_path.exists() else 0

        # Wait for OS to reclaim memory from the previous subprocess.
        pct, _ = check_memory()
        while pct > MEM_SETTLE_PERCENT:
            print(f"  Waiting for memory to settle ({pct:.0f}%)...", flush=True)
            time.sleep(3)
            pct, _ = check_memory()

        pct, avail_gb = check_memory()
        print(f"\n{'='*60}")
        print(f"  Step 2/3: {script_path.stem} [{i}/{total_metrics}] ({pct:.0f}% mem, {avail_gb:.1f} GB free)")
        print(f"{'='*60}", flush=True)

        name, elapsed, success = run_script_with_mem_guard(script_path)

        # If the process was killed but the output was written, treat as success.
        if not success and output_path.exists():
            new_mtime = output_path.stat().st_mtime
            if new_mtime > old_mtime:
                print(f"\n  [OK*] {name} ({elapsed:.1f}s) — killed but output was written", flush=True)
                continue

        print(f"\n  [{'OK' if success else 'FAILED'}] {name} ({elapsed:.1f}s)", flush=True)
        if not success:
            sys.exit(1)

    # --- Step 3: Aggregate ---
    pct, _ = check_memory()
    while pct > MEM_SETTLE_PERCENT:
        print(f"  Waiting for memory to settle ({pct:.0f}%)...", flush=True)
        time.sleep(3)
        pct, _ = check_memory()

    pct, avail_gb = check_memory()
    print(f"\n{'='*60}")
    print(f"  Step 3/3: aggregate_score ({pct:.0f}% mem)")
    print(f"{'='*60}", flush=True)
    name, elapsed, success = run_script_with_mem_guard(AGGREGATE)
    print(f"\n  [{'OK' if success else 'FAILED'}] {name} ({elapsed:.1f}s)", flush=True)
    if not success:
        sys.exit(1)

    elapsed_total = time.time() - t_total
    print(f"\n{'='*60}")
    print(f"  Signal 1 done in {elapsed_total:.1f}s ({elapsed_total/60:.1f} min)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
