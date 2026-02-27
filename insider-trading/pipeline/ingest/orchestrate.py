"""Orchestrate the full ingest pipeline: markets -> goldsky -> live processing."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.ingest.markets import update_markets
from pipeline.ingest.goldsky import update_goldsky
from pipeline.ingest.live import process_live

if __name__ == "__main__":
    print("Updating markets")
    update_markets()
    print("Updating goldsky")
    update_goldsky()
    print("Processing live")
    process_live()
