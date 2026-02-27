import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data" / "ingest")))


def get_markets(main_file: str = None, missing_file: str = None):
    if main_file is None:
        main_file = str(DATA_DIR / "markets.csv")
    if missing_file is None:
        missing_file = str(DATA_DIR / "missing_markets.csv")
    """
    Load and combine markets from both files, deduplicate, and sort by createdAt.
    Returns combined Polars DataFrame sorted by creation date.
    """
    schema_overrides = {
        "token1": pl.Utf8,
        "token2": pl.Utf8,
    }

    dfs = []

    if os.path.exists(main_file):
        main_df = pl.scan_csv(main_file, schema_overrides=schema_overrides).collect(streaming=True)
        dfs.append(main_df)
        print(f"Loaded {len(main_df)} markets from {main_file}")

    if os.path.exists(missing_file):
        missing_df = pl.scan_csv(missing_file, schema_overrides=schema_overrides).collect(streaming=True)
        dfs.append(missing_df)
        print(f"Loaded {len(missing_df)} markets from {missing_file}")

    if not dfs:
        print("No market files found!")
        return pl.DataFrame()

    combined_df = (
        pl.concat(dfs)
        .unique(subset=['id'], keep='first')
        .sort('createdAt')
    )

    print(f"Combined total: {len(combined_df)} unique markets (sorted by createdAt)")
    return combined_df
