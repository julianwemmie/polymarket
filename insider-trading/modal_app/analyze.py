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

S1_OUTPUT = f"{VOL_PATH}/analyze/signal1"
S2_OUTPUT = f"{VOL_PATH}/analyze/signal2"


def _run_script(script_path: str, output_dir: str):
    """Run an analysis script with volume-mounted paths."""
    import subprocess
    import os

    os.makedirs(output_dir, exist_ok=True)
    env = {
        **os.environ,
        "POLYMARKET_DATA_DIR": VOL_PATH,
        "POLYMARKET_OUTPUT_DIR": output_dir,
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
def s1_build_positions():
    _run_script("/app/signal1/build_positions.py", S1_OUTPUT)


@app.function(
    image=s1_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=3600,
)
def s1_metric(script_name: str):
    _run_script(f"/app/signal1/{script_name}", S1_OUTPUT)


@app.function(
    image=s1_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=3600,
)
def s1_aggregate():
    _run_script("/app/signal1/aggregate.py", S1_OUTPUT)


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
def s2_step(script_name: str):
    _run_script(f"/app/signal2/{script_name}", S2_OUTPUT)


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


def run_signal1():
    print("=== Signal 1: Implausibility ===")

    print("Step 1/3: Building wallet positions...")
    s1_build_positions.remote()

    print(f"Step 2/3: Running {len(SIGNAL1_METRICS)} metrics in parallel...")
    handles = [s1_metric.spawn(m) for m in SIGNAL1_METRICS]
    for h in handles:
        h.get()

    print("Step 3/3: Aggregating scores...")
    s1_aggregate.remote()

    print("Signal 1 complete!")


def run_signal2():
    print("=== Signal 2: Timing ===")

    for i, script in enumerate(SIGNAL2_CHAIN, 1):
        print(f"Step {i}/{len(SIGNAL2_CHAIN)}: {script}...")
        s2_step.remote(script)

    print("Signal 2 complete!")


@app.local_entrypoint()
def main(signal: str = "all"):
    import threading

    if signal == "all":
        t1 = threading.Thread(target=run_signal1)
        t2 = threading.Thread(target=run_signal2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    elif signal == "1":
        run_signal1()
    elif signal == "2":
        run_signal2()
    else:
        raise ValueError(f"Unknown signal: {signal}. Use 'all', '1', or '2'.")

    print("\nAll analysis complete!")
