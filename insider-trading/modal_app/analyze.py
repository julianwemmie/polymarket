"""
Modal cloud runner for Polymarket insider-trading analysis.

Runs Signal 1 (implausibility) and Signal 2 (timing) pipelines on Modal
with 64 GB RAM, eliminating local memory constraints.

Prerequisites (run scrape + ingest first):
  modal run modal_app/scrape.py --task all
  modal run modal_app/ingest.py

Run:
  modal run modal_app/analyze.py                # run both signals
  modal run modal_app/analyze.py --signal 1     # signal 1 only
  modal run modal_app/analyze.py --signal 2     # signal 2 only

Download results:
  modal volume get polymarket-data /analyze/signal1/ ./data/analyze/signal1/
  modal volume get polymarket-data /analyze/signal2/ ./data/analyze/signal2/
"""

from typing import Optional

import modal

from modal_app.common import vol, analysis_image, VOL_PATH

app = modal.App("polymarket-analysis")

s1_image = (
    analysis_image
    .add_local_dir("pipeline/analyze/signal1", remote_path="/app/signal1")
)
s2_image = (
    analysis_image
    .add_local_dir("pipeline/analyze/signal2", remote_path="/app/signal2")
)

def _output_dirs(base: str = VOL_PATH):
    return f"{base}/analyze/signal1", f"{base}/analyze/signal2"


def _run_script(script_path: str, output_dir: str, data_dir: str = VOL_PATH):
    """Run an analysis script with volume-mounted paths."""
    import subprocess
    import os

    os.makedirs(output_dir, exist_ok=True)
    env = {
        **os.environ,
        "POLYMARKET_DATA_DIR": data_dir,
    }
    result = subprocess.run(["python", script_path], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{script_path} failed (exit {result.returncode})")
    vol.commit()


# ---------------------------------------------------------------------------
# Signal 1: Implausibility
# ---------------------------------------------------------------------------

@app.function(
    image=s1_image,
    volumes={VOL_PATH: vol},
    cpu=8,
    memory=65536,
    timeout=7200,
)
def s1_build_positions(output_base: str = VOL_PATH):
    s1_out, _ = _output_dirs(output_base)
    _run_script("/app/signal1/build_positions.py", s1_out, data_dir=output_base)


@app.function(
    image=s1_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=3600,
)
def s1_metric(script_name: str, output_base: str = VOL_PATH):
    s1_out, _ = _output_dirs(output_base)
    _run_script(f"/app/signal1/{script_name}", s1_out, data_dir=output_base)


@app.function(
    image=s1_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=3600,
)
def s1_aggregate(output_base: str = VOL_PATH):
    s1_out, _ = _output_dirs(output_base)
    _run_script("/app/signal1/aggregate.py", s1_out, data_dir=output_base)


# ---------------------------------------------------------------------------
# Signal 2: Timing
# ---------------------------------------------------------------------------

@app.function(
    image=s2_image,
    volumes={VOL_PATH: vol},
    cpu=8,
    memory=65536,
    timeout=7200,
)
def s2_step(script_name: str, output_base: str = VOL_PATH):
    _, s2_out = _output_dirs(output_base)
    _run_script(f"/app/signal2/{script_name}", s2_out, data_dir=output_base)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SIGNAL1_METRICS = [
    "roi.py",
    "brier_score.py",
    "contrarian_win_rate.py",
    "niche_market_accuracy.py",
    "position_concentration.py",
    "profit_factor.py",
    "bet_size_vs_odds.py",
    "win_streak.py",
]

SIGNAL2_CHAIN = [
    "build_price_history.py",
    "detect_price_spikes.py",
    "pre_spike_wallets.py",
    "timing_score.py",
]


def run_signal1(output_base: str = VOL_PATH):
    print("=== Signal 1: Implausibility ===")

    print("Step 1/3: Building wallet positions...")
    s1_build_positions.remote(output_base=output_base)

    print(f"Step 2/3: Running {len(SIGNAL1_METRICS)} metrics in parallel...")
    handles = [s1_metric.spawn(m, output_base=output_base) for m in SIGNAL1_METRICS]
    for h in handles:
        h.get()

    print("Step 3/3: Aggregating scores...")
    s1_aggregate.remote(output_base=output_base)

    print("Signal 1 complete!")


def run_signal2(output_base: str = VOL_PATH):
    print("=== Signal 2: Timing ===")

    for i, script in enumerate(SIGNAL2_CHAIN, 1):
        print(f"Step {i}/{len(SIGNAL2_CHAIN)}: {script}...")
        s2_step.remote(script, output_base=output_base)

    print("Signal 2 complete!")


@app.local_entrypoint()
def main(signal: str = "all", output_dir: Optional[str] = None):
    import threading

    out = output_dir or VOL_PATH

    print(f"  Output base: {out}")

    if signal == "all":
        t1 = threading.Thread(target=run_signal1, args=(out,))
        t2 = threading.Thread(target=run_signal2, args=(out,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    elif signal == "1":
        run_signal1(output_base=out)
    elif signal == "2":
        run_signal2(output_base=out)
    else:
        raise ValueError(f"Unknown signal: {signal}. Use 'all', '1', or '2'.")

    print("\nAll analysis complete!")
