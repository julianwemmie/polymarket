#!/usr/bin/env python3
"""Run the polymarket insider-trading pipeline on Modal.

Each run writes to a dated directory on the volume: /vol/runs/YYYY-MM-DD/
so existing data at /vol/ is never overwritten.

Usage:
    python3 run.py scrape          # historical + markets + gap (5 containers x 20 workers)
    python3 run.py scrape-gap      # gap scrape only
    python3 run.py scrape-hist     # historical download only
    python3 run.py scrape-markets  # market metadata only
    python3 run.py scan            # quick volume scan
    python3 run.py scan-full       # full volume scan (row counts)
    python3 run.py ingest          # process trades from scrape data
    python3 run.py analyze         # signal 1 + signal 2
    python3 run.py analyze-s1      # signal 1 only
    python3 run.py analyze-s2      # signal 2 only
    python3 run.py all             # full pipeline: scrape → ingest → analyze
"""

import subprocess
import sys
from datetime import date

BASE_DIR = f"/vol/runs/{date.today().isoformat()}"

STAGES = {
    "scrape":         [sys.executable, "-m", "modal", "run", "modal_app/scrape.py", "--task", "all", "--output-dir", BASE_DIR],
    "scrape-gap":     [sys.executable, "-m", "modal", "run", "modal_app/scrape.py", "--task", "gap", "--output-dir", BASE_DIR],
    "scrape-hist":    [sys.executable, "-m", "modal", "run", "modal_app/scrape.py", "--task", "historical", "--output-dir", BASE_DIR],
    "scrape-markets": [sys.executable, "-m", "modal", "run", "modal_app/scrape.py", "--task", "markets", "--output-dir", BASE_DIR],
    "scan":           [sys.executable, "-m", "modal", "run", "modal_app/scan.py"],
    "scan-full":      [sys.executable, "-m", "modal", "run", "modal_app/scan.py", "--full"],
    "ingest":         [sys.executable, "-m", "modal", "run", "modal_app/ingest.py", "--output-dir", BASE_DIR],
    "analyze":        [sys.executable, "-m", "modal", "run", "modal_app/analyze.py", "--output-dir", BASE_DIR],
    "analyze-s1":     [sys.executable, "-m", "modal", "run", "modal_app/analyze.py", "--signal", "1", "--output-dir", BASE_DIR],
    "analyze-s2":     [sys.executable, "-m", "modal", "run", "modal_app/analyze.py", "--signal", "2", "--output-dir", BASE_DIR],
}

PIPELINES = {
    "all": ["scrape", "ingest", "analyze"],
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print(f"Output dir: {BASE_DIR}\n")
        print("Stages:")
        for name in STAGES:
            print(f"  {name}")
        print("\nPipelines:")
        for name, steps in PIPELINES.items():
            print(f"  {name:16s} → {' → '.join(steps)}")
        sys.exit(0)

    stage = sys.argv[1]

    if stage in PIPELINES:
        steps = PIPELINES[stage]
        total = len(steps)
        for i, step in enumerate(steps, 1):
            print(f"\n{'=' * 60}")
            print(f"[{i}/{total}] {step}")
            print(f"{'=' * 60}\n")
            rc = subprocess.run(STAGES[step]).returncode
            if rc != 0:
                print(f"\nFailed at stage '{step}' (exit {rc})")
                sys.exit(rc)
        print(f"\n{'=' * 60}")
        print(f"Pipeline complete! Data at {BASE_DIR}")
        print(f"{'=' * 60}")
        sys.exit(0)

    if stage not in STAGES:
        print(f"Unknown stage: {stage}")
        print(f"Available: {', '.join(list(STAGES) + list(PIPELINES))}")
        sys.exit(1)

    cmd = STAGES[stage]
    # Pass through any extra args (e.g. --containers 10)
    cmd = cmd + sys.argv[2:]

    print(f"Output dir: {BASE_DIR}")
    print(f"Running: {' '.join(cmd)}\n")
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
