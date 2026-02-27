#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["psutil>=5.9.0"]
# ///
"""
Run all signal2 (timing) analysis scripts.

Order (sequential — each step depends on the previous):
  1. build_price_history   (reads trades.csv)
  2. detect_price_spikes   (reads price_history.parquet)
  3. pre_spike_wallets     (reads price_spikes.parquet + trades.csv)
  4. timing_score          (reads pre_spike_trades.parquet + trades.csv)

Memory safety:
  - Warns at 85%, kills at 93%.
  - Waits between scripts for memory to settle.

Usage:
  cd pipeline/analyze/signal2
  uv run run_all.py
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
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(DATA_ROOT / "analyze" / "signal2")))

CHAIN = [
    ("build_price_history.py",  "price_history.parquet"),
    ("detect_price_spikes.py",  "price_spikes.parquet"),
    ("pre_spike_wallets.py",    "pre_spike_trades.parquet"),
    ("timing_score.py",         "timing_scores.parquet"),
]

MEM_WARN_PERCENT = 85
MEM_KILL_PERCENT = 93
MEM_CHECK_INTERVAL = 2  # seconds
MEM_SETTLE_PERCENT = 65


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
    t_total = time.time()

    pct, avail_gb = check_memory()
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    print("Signal 2: Timing Analysis")
    print(f"RAM: {total_gb:.0f} GB total, {avail_gb:.1f} GB free ({pct:.0f}% used)")
    print(f"Memory: warn at {MEM_WARN_PERCENT}%, kill at {MEM_KILL_PERCENT}%", flush=True)

    total_steps = len(CHAIN)
    for i, (script_name, output_name) in enumerate(CHAIN, 1):
        script_path = SCRIPT_DIR / script_name
        output_path = OUTPUT_DIR / output_name

        old_mtime = output_path.stat().st_mtime if output_path.exists() else 0

        # Wait for OS to reclaim memory from the previous subprocess.
        pct, _ = check_memory()
        while pct > MEM_SETTLE_PERCENT:
            print(f"  Waiting for memory to settle ({pct:.0f}%)...", flush=True)
            time.sleep(3)
            pct, _ = check_memory()

        pct, avail_gb = check_memory()
        print(f"\n{'='*60}")
        print(f"  Step {i}/{total_steps}: {script_path.stem} ({pct:.0f}% mem, {avail_gb:.1f} GB free)")
        print(f"{'='*60}", flush=True)

        name, elapsed, success = run_script_with_mem_guard(script_path)

        if not success and output_path.exists():
            new_mtime = output_path.stat().st_mtime
            if new_mtime > old_mtime:
                print(f"\n  [OK*] {name} ({elapsed:.1f}s) — killed but output was written", flush=True)
                continue

        print(f"\n  [{'OK' if success else 'FAILED'}] {name} ({elapsed:.1f}s)", flush=True)
        if not success:
            sys.exit(1)

    elapsed_total = time.time() - t_total
    print(f"\n{'='*60}")
    print(f"  Signal 2 done in {elapsed_total:.1f}s ({elapsed_total/60:.1f} min)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
