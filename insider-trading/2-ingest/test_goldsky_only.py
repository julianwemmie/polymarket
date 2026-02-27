"""Quick test - just goldsky scrape + processing (markets already fetched)."""
from update_utils.update_goldsky import update_goldsky
from update_utils.process_live import process_live
import os

if __name__ == "__main__":
    lines = sum(1 for _ in open("markets.csv")) - 1
    print(f"Markets already fetched: {lines:,}")

    print("\n[1/2] Fetching order events from Goldsky (5 batches = ~5,000 events)...")
    update_goldsky(max_batches=5)

    # Check what we got
    goldsky_file = "goldsky/orderFilled.csv"
    if os.path.exists(goldsky_file):
        goldsky_lines = sum(1 for _ in open(goldsky_file)) - 1
        print(f"\nGoldsky events fetched: {goldsky_lines:,}")

        # Show first few lines
        import pandas as pd
        df = pd.read_csv(goldsky_file, nrows=5)
        print("\nSample orderFilled data:")
        print(df.to_string())

    print("\n[2/2] Processing trades (joining events with markets)...")
    try:
        process_live()

        processed_file = "processed/trades.csv"
        if os.path.exists(processed_file):
            pdf = pd.read_csv(processed_file, nrows=5)
            print(f"\nSample processed trades:")
            print(pdf.to_string())

            total = sum(1 for _ in open(processed_file)) - 1
            print(f"\nTotal processed trades: {total:,}")
    except Exception as e:
        print(f"Processing error: {e}")
        import traceback
        traceback.print_exc()

    print("\nTest complete!")
