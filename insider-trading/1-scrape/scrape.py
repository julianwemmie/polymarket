"""
Parallel Goldsky orderFilledEvents scraper.

Splits the gap period into equal-event partitions and scrapes each partition
concurrently using asyncio + aiohttp. Each worker writes to its own gzipped
CSV chunk with streaming compression and cursor-based resume.

Usage:
    uv run python scrape.py                 # 20 workers, fresh start
    uv run python scrape.py --workers 10    # 10 workers
    uv run python scrape.py --resume        # resume from saved cursor state
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
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
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)

BATCH_SIZE = 1000  # hard limit from Goldsky

# Gap boundaries
DEFAULT_GAP_START_TS = 1759855190   # 2025-10-07 16:39:50 UTC (last record in archive)
GAP_START_TS = DEFAULT_GAP_START_TS

# Number of density probes to estimate event counts across the gap
DENSITY_PROBE_COUNT = 40

# CSV columns (matching existing schema)
CSV_COLUMNS = [
    "id",
    "timestamp",
    "maker",
    "makerAssetId",
    "makerAmountFilled",
    "taker",
    "takerAssetId",
    "takerAmountFilled",
    "transactionHash",
]

# Retry settings
MAX_RETRIES = 12
BASE_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 120.0  # seconds

# Concurrency throttle: max in-flight API requests across all workers
MAX_CONCURRENT_REQUESTS = 25

# Directories
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
CHUNKS_DIR = DATA_ROOT / "scrape"
CURSORS_DIR = DATA_ROOT / "scrape" / "cursors"
MANIFEST_PATH = DATA_ROOT / "scrape" / "manifest.json"
LOG_PATH = SCRIPT_DIR / "scrape.log"

# Progress display settings
PROGRESS_INTERVAL = 1.5  # seconds between display refreshes
MAX_RECENT_LOGS = 15     # max recent log lines kept in deque


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger("scrape")
logger.setLevel(logging.DEBUG)

# File handler -- append mode so resume runs are captured
_file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(_file_handler)


# ---------------------------------------------------------------------------
# Progress state (shared across workers and display task)
# ---------------------------------------------------------------------------

@dataclass
class WorkerState:
    worker_id: int
    rows: int = 0
    batches: int = 0
    status: str = "pending"   # pending, active, done, failed
    rate: float = 0.0         # rows/s
    start_time: float = 0.0
    last_ts_str: str = ""     # human-readable last timestamp


@dataclass
class ProgressState:
    num_workers: int = 0
    workers: dict[int, WorkerState] = field(default_factory=dict)
    total_rows: int = 0           # sum across all workers
    estimated_total: int = 0      # from density estimates
    start_time: float = 0.0
    recent_logs: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=MAX_RECENT_LOGS)
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: str = "init"           # init, probing, scraping, done
    display_active: bool = False  # whether the ANSI display is running


# Global progress state -- created in main()
progress: ProgressState | None = None


def add_recent_log(msg: str) -> None:
    """Append a timestamped message to the recent log deque (thread-safe enough for asyncio)."""
    if progress is not None:
        ts = datetime.now().strftime("%H:%M:%S")
        progress.recent_logs.append(f"  [{ts}] {msg}")


# ---------------------------------------------------------------------------
# Terminal display
# ---------------------------------------------------------------------------

def _get_term_width() -> int:
    """Get terminal width, defaulting to 80 if unavailable."""
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:
        return 80


def _format_duration(seconds: float) -> str:
    """Format seconds into Xh Ym Zs."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _build_progress_bar(fraction: float, width: int) -> str:
    """Build a text progress bar like [########............]."""
    filled = int(fraction * width)
    filled = max(0, min(filled, width))
    empty = width - filled
    return f"[{'#' * filled}{'.' * empty}]"


def render_display() -> str:
    """Render the full progress display as a string (no ANSI codes yet)."""
    if progress is None:
        return ""

    tw = _get_term_width()
    bar_width = min(40, tw - 20)
    lines: list[str] = []

    sep = "=" * min(60, tw)
    lines.append(sep)

    elapsed = time.monotonic() - progress.start_time if progress.start_time else 0.0

    # Compute aggregate stats
    active = sum(1 for w in progress.workers.values() if w.status == "active")
    done = sum(1 for w in progress.workers.values() if w.status == "done")
    failed = sum(1 for w in progress.workers.values() if w.status == "failed")
    total_workers = progress.num_workers

    total_rows = sum(w.rows for w in progress.workers.values())
    total_rate = sum(w.rate for w in progress.workers.values() if w.status == "active")

    est = progress.estimated_total
    fraction = (total_rows / est) if est > 0 else 0.0
    fraction = min(fraction, 1.0)
    pct = fraction * 100

    # ETA
    if total_rate > 0 and est > total_rows:
        eta_secs = (est - total_rows) / total_rate
        eta_str = f"~{_format_duration(eta_secs)}"
    elif fraction >= 1.0:
        eta_str = "done"
    else:
        eta_str = "calculating..."

    bar = _build_progress_bar(fraction, bar_width)

    lines.append(f"  Progress: {bar} {pct:5.1f}%")
    lines.append(
        f"  Workers:  {active}/{total_workers} active  |  "
        f"{done}/{total_workers} done  |  {failed} failed"
    )
    lines.append(
        f"  Rows:     {total_rows:,} / ~{est:,}"
    )
    lines.append(
        f"  Speed:    {total_rate:,.0f} rows/s (across all workers)"
    )
    lines.append(
        f"  Elapsed:  {_format_duration(elapsed)}  |  ETA: {eta_str}"
    )
    lines.append(sep)

    # Recent log lines
    for log_line in progress.recent_logs:
        lines.append(log_line)

    return "\n".join(lines)


async def progress_monitor(stop_event: asyncio.Event) -> None:
    """Async task that redraws the terminal display every PROGRESS_INTERVAL seconds."""
    if progress is None:
        return

    # Wait until we're actually scraping
    while progress.phase != "scraping" and not stop_event.is_set():
        await asyncio.sleep(0.2)

    progress.display_active = True

    # Initial clear
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    try:
        while not stop_event.is_set():
            display = render_display()
            # Move cursor to top-left, clear screen below, then write
            sys.stdout.write("\033[H\033[J")
            sys.stdout.write(display)
            sys.stdout.write("\n")
            sys.stdout.flush()

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=PROGRESS_INTERVAL)
                break  # event was set
            except asyncio.TimeoutError:
                pass
    finally:
        progress.display_active = False
        # Final clear of ANSI state -- move cursor below display and reset
        sys.stdout.write("\033[H\033[J")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def build_query(where_clause: str, first: int = BATCH_SIZE) -> str:
    """Build a GraphQL query string for orderFilledEvents."""
    return json.dumps({
        "query": f"""{{
            orderFilledEvents(
                orderBy: timestamp,
                orderDirection: asc,
                first: {first},
                where: {{{where_clause}}}
            ) {{
                id
                timestamp
                maker
                makerAssetId
                makerAmountFilled
                taker
                takerAssetId
                takerAmountFilled
                transactionHash
            }}
        }}"""
    })


async def graphql_post(
    session: aiohttp.ClientSession,
    payload: str,
    worker_id: str = "probe",
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Execute a GraphQL POST with exponential backoff on errors.

    If *semaphore* is provided, it is acquired before each HTTP request to
    throttle global concurrency across all workers.
    """
    headers = {"Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        try:
            if semaphore is not None:
                await semaphore.acquire()
            try:
                async with session.post(
                    GOLDSKY_URL, data=payload, headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "errors" in data:
                            # Bug 14: retry GraphQL errors with backoff
                            backoff = min(
                                BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF
                            )
                            msg = (
                                f"[{worker_id}] GraphQL error: {str(data['errors'])[:200]}, "
                                f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                            )
                            logger.warning(msg)
                            add_recent_log(msg)
                            await asyncio.sleep(backoff)
                            continue
                        return data

                    if resp.status in (429, 500, 502, 503, 504):
                        backoff = min(
                            BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF
                        )
                        text = await resp.text()
                        msg = (
                            f"[{worker_id}] HTTP {resp.status}, "
                            f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                        )
                        logger.warning(msg)
                        add_recent_log(msg)
                        await asyncio.sleep(backoff)
                        continue

                    text = await resp.text()
                    raise RuntimeError(
                        f"HTTP {resp.status}: {text[:200]}"
                    )
            finally:
                if semaphore is not None:
                    semaphore.release()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            msg = (
                f"[{worker_id}] Connection error: {exc}, "
                f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})"
            )
            logger.warning(msg)
            add_recent_log(msg)
            await asyncio.sleep(backoff)

    raise RuntimeError(f"[{worker_id}] Failed after {MAX_RETRIES} retries")


# ---------------------------------------------------------------------------
# Density probing & partitioning
# ---------------------------------------------------------------------------

async def probe_count_at(
    session: aiohttp.ClientSession,
    timestamp: int,
    semaphore: asyncio.Semaphore | None = None,
) -> float:
    """Estimate the event rate (events/hour) at a given timestamp using
    adaptive window sizing.

    Starts with a short window and uses binary search to find a window
    duration that returns ~500 events (between 200-800), then extrapolates
    the hourly rate from that. This avoids the 1000-result cap that made
    the old fixed 1-hour probe underestimate high-density periods.
    """
    TARGET_LOW = 200
    TARGET_HIGH = 800
    MAX_PROBE_ROUNDS = 8

    # Start with a 60-second window
    window_secs = 60
    window_min = 1       # 1 second floor
    window_max = 7200    # 2 hour ceiling

    for _ in range(MAX_PROBE_ROUNDS):
        payload = build_query(
            f'timestamp_gte: "{timestamp}", timestamp_lt: "{timestamp + window_secs}"',
            first=BATCH_SIZE,
        )
        data = await graphql_post(session, payload, worker_id="probe", semaphore=semaphore)
        count = len(data.get("data", {}).get("orderFilledEvents", []))

        if count == 0:
            # No events at all -- expand aggressively
            window_min = window_secs
            window_secs = min(window_secs * 4, window_max)
            if window_secs >= window_max:
                # Truly sparse region
                return 0.0
            continue

        if TARGET_LOW <= count <= TARGET_HIGH:
            # Good range -- extrapolate
            return count * (3600.0 / window_secs)

        if count >= BATCH_SIZE:
            # Hit the cap -- window too wide, shrink
            window_max = window_secs
            window_secs = max((window_min + window_secs) // 2, window_min + 1)
        elif count < TARGET_LOW:
            # Too few -- expand
            window_min = window_secs
            window_secs = min((window_secs + window_max) // 2, window_max)
        else:
            # count > TARGET_HIGH but < BATCH_SIZE: slightly shrink
            window_max = window_secs
            window_secs = max((window_min + window_secs) // 2, window_min + 1)

    # Exhausted rounds -- best-effort extrapolation from last result
    if count > 0:
        return min(count, BATCH_SIZE) * (3600.0 / window_secs)
    return 0.0


async def estimate_partitions(
    session: aiohttp.ClientSession,
    num_workers: int,
    gap_end_ts: int,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[list[tuple[int, int]], int]:
    """Probe density across the gap and create equal-event partitions.

    Returns a tuple of (partitions, estimated_total) where partitions is a
    list of (start_ts, end_ts) tuples, one per worker, and estimated_total
    is the approximate number of events in the gap period.
    """
    msg = f"Probing event density across gap period..."
    logger.info(msg)
    logger.info(f"  Gap: {ts_to_str(GAP_START_TS)} -> {ts_to_str(gap_end_ts)}")
    logger.info(f"  Duration: {(gap_end_ts - GAP_START_TS) / 86400:.1f} days")

    # Probe density at evenly-spaced points across the gap
    gap_duration = gap_end_ts - GAP_START_TS
    probe_interval = gap_duration / DENSITY_PROBE_COUNT

    probe_tasks = []
    probe_timestamps = []
    for i in range(DENSITY_PROBE_COUNT):
        ts = int(GAP_START_TS + i * probe_interval)
        probe_timestamps.append(ts)
        probe_tasks.append(probe_count_at(session, ts, semaphore=semaphore))

    probe_results = await asyncio.gather(*probe_tasks)

    # Build a cumulative density curve
    # Each probe gives us estimated events/hour at that point (adaptive)
    densities = []  # (timestamp, events_per_hour)
    for ts, rate in zip(probe_timestamps, probe_results):
        events_per_hour = rate
        densities.append((ts, events_per_hour))
        date_str = ts_to_str(ts)
        logger.info(f"  {date_str}: ~{events_per_hour:,.0f} events/hour")

    # Integrate to get approximate cumulative events at each probe point
    cumulative = [0.0]
    for i in range(1, len(densities)):
        dt_hours = (densities[i][0] - densities[i - 1][0]) / 3600.0
        avg_rate = (densities[i - 1][1] + densities[i][1]) / 2.0
        cumulative.append(cumulative[-1] + avg_rate * dt_hours)

    # Add the final segment from last probe to gap_end_ts
    final_dt_hours = (gap_end_ts - densities[-1][0]) / 3600.0
    total_estimate = cumulative[-1] + densities[-1][1] * final_dt_hours

    logger.info(f"  Estimated total events in gap: ~{total_estimate:,.0f}")
    events_per_worker = total_estimate / num_workers
    logger.info(f"  Target events per worker: ~{events_per_worker:,.0f}")

    # Find partition boundaries by walking the cumulative curve
    partitions = []
    target_cumulative = 0.0
    partition_start = GAP_START_TS

    for w in range(num_workers - 1):
        target_cumulative += events_per_worker

        # Find the timestamp where cumulative events reaches target
        partition_end = gap_end_ts  # default
        for i in range(1, len(cumulative)):
            if cumulative[i] >= target_cumulative:
                # Linear interpolation between probe points
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
    partitions.append((partition_start, gap_end_ts))

    # Bug 2: Merge zero-length or very short partitions (< 60 seconds)
    # that arise from sparse regions where the cumulative curve is flat.
    MIN_PARTITION_SECS = 60
    merged = []
    for start, end in partitions:
        if merged and (end - start) < MIN_PARTITION_SECS:
            # Absorb into the previous partition
            merged[-1] = (merged[-1][0], end)
        elif (end - start) < MIN_PARTITION_SECS and not merged:
            # First partition is too short -- will be merged with the next
            merged.append((start, end))
        else:
            # Check if the previous partition (just added) was too short
            if merged and (merged[-1][1] - merged[-1][0]) < MIN_PARTITION_SECS:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))

    if len(merged) < len(partitions):
        removed = len(partitions) - len(merged)
        logger.warning(
            f"Merged {removed} zero-length/short partition(s) "
            f"from sparse regions. Worker count reduced from "
            f"{len(partitions)} to {len(merged)}."
        )
    partitions = merged

    logger.info(f"Partition plan ({len(partitions)} workers):")
    for i, (start, end) in enumerate(partitions):
        duration_days = (end - start) / 86400
        logger.info(
            f"  Worker {i:02d}: {ts_to_str(start)} -> {ts_to_str(end)} "
            f"({duration_days:.1f} days)"
        )

    return partitions, int(total_estimate)


# ---------------------------------------------------------------------------
# Cursor state management
# ---------------------------------------------------------------------------

def cursor_path(worker_id: int) -> Path:
    return CURSORS_DIR / f"worker_{worker_id:02d}.json"


def load_cursor(worker_id: int) -> dict | None:
    """Load saved cursor state for a worker."""
    path = cursor_path(worker_id)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_cursor_state(
    worker_id: int,
    last_timestamp: int,
    last_id: str | None,
    sticky_timestamp: int | None,
    rows_written: int,
    part_number: int,
    partition_start_ts: int,
    partition_end_ts: int,
) -> None:
    """Save cursor state for a worker, including partition boundaries."""
    CURSORS_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "last_timestamp": last_timestamp,
        "last_id": last_id,
        "sticky_timestamp": sticky_timestamp,
        "rows_written": rows_written,
        "part_number": part_number,
        "partition_start_ts": partition_start_ts,
        "partition_end_ts": partition_end_ts,
    }
    with open(cursor_path(worker_id), "w") as f:
        json.dump(state, f)


def clear_cursor(worker_id: int) -> None:
    """Remove cursor file for a completed worker."""
    path = cursor_path(worker_id)
    if path.exists():
        path.unlink()


def clear_all_cursors() -> None:
    """Remove all cursor files (used when partition plan changes)."""
    if CURSORS_DIR.exists():
        for p in CURSORS_DIR.glob("worker_*.json"):
            p.unlink()
        logger.info("Cleared all stale cursor files (partition plan changed).")


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    """Load or create the manifest."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"workers": {}, "created": datetime.now(tz=timezone.utc).isoformat()}


def save_manifest(manifest: dict) -> None:
    """Save the manifest atomically via temp file + rename."""
    manifest["updated"] = datetime.now(tz=timezone.utc).isoformat()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=MANIFEST_PATH.parent, suffix=".tmp", prefix="manifest_"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(manifest, f, indent=2)
        os.rename(tmp_path, MANIFEST_PATH)
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def ts_to_str(ts: int) -> str:
    """Convert a Unix timestamp to a human-readable UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def ts_to_date(ts: int) -> str:
    """Convert a Unix timestamp to YYYYMMDD."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")


def file_md5(path: Path) -> str:
    """Compute MD5 checksum of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def worker(
    session: aiohttp.ClientSession,
    worker_id: int,
    start_ts: int,
    end_ts: int,
    resume: bool,
    semaphore: asyncio.Semaphore | None = None,
    max_batches: int | None = None,
) -> dict:
    """Scrape a single time partition and write to a gzipped CSV chunk.

    Returns metadata dict for the manifest.
    """
    wid = f"W{worker_id:02d}"

    # Initialize worker in progress state
    if progress is not None:
        progress.workers[worker_id] = WorkerState(
            worker_id=worker_id,
            status="active",
            start_time=time.monotonic(),
        )

    # Resume from cursor if available
    last_timestamp = start_ts
    last_id: str | None = None
    sticky_timestamp: int | None = None
    rows_written = 0
    part_number = 1
    # Track whether we've moved past the initial partition start.
    # On the very first batch of a fresh start we use timestamp_gte to include
    # the partition boundary. After the first batch we switch to timestamp_gt.
    past_start = False

    if resume:
        cursor = load_cursor(worker_id)
        if cursor:
            # Bug 5: Validate cursor partition boundaries match current plan
            cursor_start = cursor.get("partition_start_ts")
            cursor_end = cursor.get("partition_end_ts")
            if cursor_start is not None and cursor_end is not None:
                if cursor_start != start_ts or cursor_end != end_ts:
                    msg = (
                        f"[{wid}] WARNING: Stale cursor (partition "
                        f"{cursor_start}-{cursor_end} != {start_ts}-{end_ts}), "
                        f"starting fresh"
                    )
                    logger.warning(msg)
                    add_recent_log(msg)
                    cursor = None
            if cursor:
                last_timestamp = cursor["last_timestamp"]
                last_id = cursor.get("last_id")
                sticky_timestamp = cursor.get("sticky_timestamp")
                rows_written = cursor.get("rows_written", 0)
                # Bug 6: Increment part number on resume instead of
                # re-reading old gzip data into memory
                old_part_number = cursor.get("part_number", 1)

                # Bug 1: Delete the old partial part file from the crashed
                # session to prevent duplicate events when concatenating parts
                old_part_pattern = (
                    f"chunk_{worker_id:02d}_{ts_to_date(start_ts)}_"
                    f"{ts_to_date(end_ts)}_part{old_part_number}.csv.gz"
                )
                old_part_path = CHUNKS_DIR / old_part_pattern
                if old_part_path.exists():
                    old_part_path.unlink()
                    msg = f"[{wid}] Deleted old partial part file: {old_part_path.name}"
                    logger.info(msg)
                    add_recent_log(msg)

                part_number = old_part_number + 1
                past_start = True  # cursor means we already consumed some data
                msg = (
                    f"[{wid}] Resuming from timestamp {last_timestamp} "
                    f"({ts_to_str(last_timestamp)}), {rows_written} rows already "
                    f"written, writing to part {part_number}"
                )
                logger.info(msg)
                add_recent_log(msg)

                # Update progress state with resumed row count
                if progress is not None:
                    progress.workers[worker_id].rows = rows_written

    # Check if chunk is already complete (exists in manifest)
    manifest = load_manifest()
    worker_key = str(worker_id)
    if worker_key in manifest.get("workers", {}):
        info = manifest["workers"][worker_key]
        if info.get("status") == "complete":
            msg = f"[{wid}] Already complete ({info.get('rows', 0)} rows), skipping"
            logger.info(msg)
            add_recent_log(msg)
            if progress is not None:
                ws = progress.workers[worker_id]
                ws.status = "done"
                ws.rows = info.get("rows", 0)
            return info

    msg = (
        f"[{wid}] Starting: {ts_to_str(start_ts)} -> {ts_to_str(end_ts)} "
        f"({(end_ts - start_ts) / 86400:.1f} days)"
    )
    logger.info(msg)
    add_recent_log(msg)

    # Bug 6: Open a NEW gzip part file (no re-reading old data)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    chunk_name = (
        f"chunk_{worker_id:02d}_{ts_to_date(start_ts)}_"
        f"{ts_to_date(end_ts)}_part{part_number}.csv.gz"
    )
    chunk_path = CHUNKS_DIR / chunk_name

    gz = gzip.open(chunk_path, "wb", compresslevel=6)
    # Bug 9: Use csv.writer for proper escaping
    gz_text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
    csv_writer = csv.writer(gz_text)
    csv_writer.writerow(CSV_COLUMNS)

    batch_count = 0
    t0 = time.monotonic()

    try:
        while True:
            # --max-batches: stop early when set (useful for testing)
            if max_batches and batch_count >= max_batches:
                break

            # Bug 1: Defensive upper-bound check for sticky_timestamp
            if sticky_timestamp is not None and sticky_timestamp >= end_ts:
                break

            # Build where clause
            if sticky_timestamp is not None:
                # Paginating within a single timestamp by ID
                where = f'timestamp: "{sticky_timestamp}", id_gt: "{last_id}"'
            elif past_start:
                # We already consumed last_timestamp, skip past it
                where = (
                    f'timestamp_gt: "{last_timestamp}", '
                    f'timestamp_lt: "{end_ts}"'
                )
            else:
                # Very first batch: include the partition start boundary
                where = (
                    f'timestamp_gte: "{last_timestamp}", '
                    f'timestamp_lt: "{end_ts}"'
                )

            payload = build_query(where)
            data = await graphql_post(
                session, payload, worker_id=wid, semaphore=semaphore
            )

            events = data.get("data", {}).get("orderFilledEvents", [])

            if not events:
                if sticky_timestamp is not None:
                    # Done with this sticky timestamp, move on
                    last_timestamp = sticky_timestamp
                    sticky_timestamp = None
                    last_id = None
                    continue
                # No more data in this partition
                break

            # Sort by timestamp, then id
            events.sort(key=lambda e: (int(e["timestamp"]), e["id"]))

            batch_first_ts = int(events[0]["timestamp"])
            batch_last_ts = int(events[-1]["timestamp"])
            batch_last_id = events[-1]["id"]

            # Bug 9: Write rows using csv.writer for proper escaping
            for event in events:
                csv_writer.writerow(
                    [str(event.get(col, "")) for col in CSV_COLUMNS]
                )
                rows_written += 1

            # Determine sticky timestamp handling
            if len(events) >= BATCH_SIZE:
                if batch_first_ts == batch_last_ts:
                    # All 1000 results have the same timestamp -- must paginate by ID
                    sticky_timestamp = batch_last_ts
                    last_id = batch_last_id
                else:
                    # Full batch spanning multiple timestamps --
                    # set sticky to the last timestamp to ensure we don't miss
                    # events at that exact second
                    sticky_timestamp = batch_last_ts
                    last_id = batch_last_id
            else:
                # Partial batch -- this timestamp is complete
                if sticky_timestamp is not None:
                    last_timestamp = sticky_timestamp
                    sticky_timestamp = None
                    last_id = None
                else:
                    last_timestamp = batch_last_ts

            batch_count += 1
            past_start = True  # after first batch, always use timestamp_gt

            # Flush csv writer then save cursor state every batch
            gz_text.flush()
            save_cursor_state(
                worker_id, last_timestamp, last_id, sticky_timestamp,
                rows_written, part_number, start_ts, end_ts,
            )

            # Update progress state every batch
            elapsed = time.monotonic() - t0
            rate = rows_written / elapsed if elapsed > 0 else 0
            if progress is not None:
                ws = progress.workers[worker_id]
                ws.rows = rows_written
                ws.batches = batch_count
                ws.rate = rate
                ws.last_ts_str = ts_to_str(batch_last_ts)

            # Log every 50 batches
            if batch_count % 50 == 0:
                msg = (
                    f"[{wid}] Batch {batch_count}: "
                    f"{ts_to_str(batch_last_ts)}, "
                    f"{rows_written:,} rows, {rate:,.0f} rows/s"
                )
                logger.info(msg)
                add_recent_log(msg)

    except Exception as exc:
        # Mark worker as failed in progress state
        if progress is not None:
            progress.workers[worker_id].status = "failed"
            progress.workers[worker_id].rate = 0.0
        msg = f"[{wid}] FAILED: {exc}"
        logger.error(msg)
        add_recent_log(msg)
        gz_text.close()
        raise

    finally:
        if not gz_text.closed:
            gz_text.close()  # also closes underlying gz

    elapsed = time.monotonic() - t0
    rate = rows_written / elapsed if elapsed > 0 else 0

    # Clear cursor on completion
    clear_cursor(worker_id)

    # Bug 3: Collect per-part metadata (file, rows, md5, file_size_bytes)
    # so the manifest checksum covers ALL parts, not just the last one.
    part_pattern = f"chunk_{worker_id:02d}_{ts_to_date(start_ts)}_{ts_to_date(end_ts)}_part*.csv.gz"
    chunk_files = sorted(CHUNKS_DIR.glob(part_pattern))

    parts_meta = []
    total_file_size = 0
    for part_path in chunk_files:
        part_size = part_path.stat().st_size
        part_md5 = file_md5(part_path)
        # Estimate rows per part: count lines in the gzipped CSV (minus header)
        with gzip.open(part_path, "rt", encoding="utf-8") as pf:
            part_rows = sum(1 for _ in pf) - 1  # subtract header
            part_rows = max(0, part_rows)
        parts_meta.append({
            "file": part_path.name,
            "rows": part_rows,
            "md5": part_md5,
            "file_size_bytes": part_size,
        })
        total_file_size += part_size

    result = {
        "worker_id": worker_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_date": ts_to_str(start_ts),
        "end_date": ts_to_str(end_ts),
        "chunk_files": [p.name for p in chunk_files],
        "parts": parts_meta,
        "rows": rows_written,
        "batches": batch_count,
        "elapsed_seconds": round(elapsed, 1),
        "rows_per_second": round(rate, 1),
        "file_size_bytes": total_file_size,
        "file_size_mb": round(total_file_size / (1024 * 1024), 2),
        "status": "complete",
    }

    # Update progress state -- mark done
    if progress is not None:
        ws = progress.workers[worker_id]
        ws.status = "done"
        ws.rows = rows_written
        ws.rate = 0.0  # no longer contributing to active rate

    msg = (
        f"[{wid}] DONE: {rows_written:,} rows in {batch_count} batches, "
        f"{elapsed:.1f}s ({rate:,.0f} rows/s), "
        f"{result['file_size_mb']:.1f} MB compressed"
        + (f" ({len(chunk_files)} parts)" if len(chunk_files) > 1 else "")
    )
    logger.info(msg)
    add_recent_log(msg)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(num_workers: int, resume: bool, max_batches: int | None = None, start_ts: int | None = None) -> None:
    global progress, GAP_START_TS

    if start_ts is not None:
        GAP_START_TS = start_ts

    # Bug 5: Compute GAP_END_TS at execution time, not import time
    gap_end_ts = int(datetime.now(tz=timezone.utc).timestamp())

    # Partition merging handles cases where workers > meaningful time slices,
    # so no need to cap here. The API concurrency limit (MAX_CONCURRENT_REQUESTS)
    # is the real throttle.

    # Initialize progress state
    progress = ProgressState(
        num_workers=num_workers,
        start_time=time.monotonic(),
        phase="init",
    )

    # Log startup info
    header = (
        f"{'=' * 70}\n"
        f"Parallel Goldsky orderFilledEvents Scraper\n"
        f"{'=' * 70}\n"
        f"  Workers:    {num_workers}\n"
        f"  Resume:     {resume}\n"
        + (f"  Max batches:{max_batches} (per worker)\n" if max_batches is not None else "")
        + f"  Gap start:  {ts_to_str(GAP_START_TS)} (ts={GAP_START_TS})\n"
        f"  Gap end:    {ts_to_str(gap_end_ts)} (ts={gap_end_ts})\n"
        f"  Batch size: {BATCH_SIZE}\n"
        f"  Output:     {CHUNKS_DIR}/\n"
        f"  Goldsky:    {GOLDSKY_URL}\n"
    )
    logger.info(header)

    # Print startup info to terminal (before ANSI display takes over)
    print("=" * 70)
    print("Parallel Goldsky orderFilledEvents Scraper")
    print("=" * 70)
    print(f"  Workers:    {num_workers}")
    print(f"  Resume:     {resume}")
    if max_batches is not None:
        print(f"  Max batches:{max_batches} (per worker)")
    print(f"  Gap start:  {ts_to_str(GAP_START_TS)} (ts={GAP_START_TS})")
    print(f"  Gap end:    {ts_to_str(gap_end_ts)} (ts={gap_end_ts})")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Output:     {CHUNKS_DIR}/")
    print(f"  Goldsky:    {GOLDSKY_URL}")
    print(f"  Log file:   {LOG_PATH}")
    print()

    # Create output directories
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    CURSORS_DIR.mkdir(parents=True, exist_ok=True)

    # Bug 13: Global concurrency throttle
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # Bug 4: Increase total timeout to 120s to avoid unnecessary retries under load
    timeout = aiohttp.ClientTimeout(total=120, connect=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        # If resuming with an existing manifest that has partitions, reuse them
        partitions = None
        estimated_total = 0

        if resume:
            manifest = load_manifest()
            if "partitions" in manifest and len(manifest["partitions"]) == num_workers:
                partitions = [
                    (p["start_ts"], p["end_ts"]) for p in manifest["partitions"]
                ]
                # Try to recover estimated total from manifest
                estimated_total = manifest.get("estimated_total", 0)
                msg = "Reusing partition plan from previous run."
                logger.info(msg)
                print(msg)
            elif "partitions" in manifest:
                # Bug 5: Worker count changed -- clear all stale cursors
                msg = (
                    f"  WARNING: Previous run had {len(manifest.get('partitions', []))} "
                    f"partitions but --workers={num_workers}. "
                    f"Re-partitioning and clearing stale cursors."
                )
                logger.warning(msg)
                print(msg)
                clear_all_cursors()

        if partitions is None:
            progress.phase = "probing"
            print("Probing event density (see log file for details)...")
            partitions, estimated_total = await estimate_partitions(
                session, num_workers, gap_end_ts, semaphore=semaphore
            )
            # Bug 2: estimate_partitions may merge short partitions,
            # so update num_workers to match the actual partition count
            num_workers = len(partitions)
            progress.num_workers = num_workers

        if estimated_total <= 0:
            # Fallback estimate if we couldn't get one
            estimated_total = 300_000_000

        # When --max-batches is set, cap the estimate so the progress bar
        # reflects the actual work being done in this run.
        if max_batches is not None:
            capped = num_workers * max_batches * BATCH_SIZE
            estimated_total = min(estimated_total, capped)

        progress.estimated_total = estimated_total

        # Save partition plan to manifest
        manifest = load_manifest()
        manifest["partitions"] = [
            {"worker_id": i, "start_ts": s, "end_ts": e}
            for i, (s, e) in enumerate(partitions)
        ]
        manifest["num_workers"] = num_workers
        manifest["gap_start_ts"] = GAP_START_TS
        manifest["gap_end_ts"] = gap_end_ts
        manifest["estimated_total"] = estimated_total
        save_manifest(manifest)

        # Launch all workers concurrently
        logger.info(f"Launching {num_workers} workers...")
        progress.phase = "scraping"
        progress.start_time = time.monotonic()

        # Start the progress monitor
        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(progress_monitor(stop_event))

        t0 = time.monotonic()

        tasks = [
            worker(session, i, start, end, resume, semaphore=semaphore, max_batches=max_batches)
            for i, (start, end) in enumerate(partitions)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Stop the progress monitor
        stop_event.set()
        await monitor_task

    progress.phase = "done"

    # Process results
    total_rows = 0
    total_size = 0
    errors = []

    manifest = load_manifest()
    if "workers" not in manifest:
        manifest["workers"] = {}

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append((i, str(result)))
            logger.error(f"[W{i:02d}] FAILED: {result}")
        else:
            manifest["workers"][str(i)] = result
            total_rows += result["rows"]
            total_size += result["file_size_bytes"]

    save_manifest(manifest)

    elapsed = time.monotonic() - t0

    # Final clean summary (no ANSI tricks)
    summary = (
        f"\n{'=' * 70}\n"
        f"SCRAPE COMPLETE\n"
        f"{'=' * 70}\n"
        f"  Total rows:       {total_rows:,}\n"
        f"  Total compressed: {total_size / (1024**3):.2f} GB\n"
        f"  Total time:       {elapsed / 60:.1f} minutes\n"
        f"  Avg throughput:   {total_rows / elapsed:,.0f} rows/s\n"
        f"  Workers:          {num_workers}\n"
    )
    if errors:
        summary += f"  Errors:           {len(errors)}\n"
        for wid, err in errors:
            summary += f"    Worker {wid}: {err}\n"
    summary += (
        f"  Manifest:         {MANIFEST_PATH}\n"
        f"  Chunks:           {CHUNKS_DIR}/\n"
        f"  Log file:         {LOG_PATH}\n"
    )

    logger.info(summary)
    print(summary)


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Parallel Goldsky orderFilledEvents scraper"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Number of concurrent workers (default: 20)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from saved cursor state",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Stop each worker after this many batches (useful for testing)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start timestamp (unix ts) or relative like '7d', '24h', '30d' for days/hours ago",
    )
    args = parser.parse_args()

    if args.workers < 1:
        print("Error: --workers must be >= 1")
        sys.exit(1)

    start_ts = None
    if args.start is not None:
        s = args.start.strip().lower()
        now = int(datetime.now(tz=timezone.utc).timestamp())
        if s.endswith("d"):
            start_ts = now - int(s[:-1]) * 86400
        elif s.endswith("h"):
            start_ts = now - int(s[:-1]) * 3600
        else:
            start_ts = int(s)

    asyncio.run(main(args.workers, args.resume, max_batches=args.max_batches, start_ts=start_ts))


if __name__ == "__main__":
    cli()
