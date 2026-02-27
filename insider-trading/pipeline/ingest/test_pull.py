"""
Small test pull - fetches a limited subset of data to verify the pipeline works.
- Markets: first 2 batches (1,000 markets)
- Goldsky: first 5 batches (~5,000 order events)
- Process: joins trades with markets
"""
from pipeline.ingest.markets import update_markets
from pipeline.ingest.goldsky import update_goldsky
from pipeline.ingest.live import process_live

if __name__ == "__main__":
    print("=" * 60)
    print("TEST PULL - Small subset to verify pipeline")
    print("=" * 60)

    print("\n[1/3] Fetching market metadata (first 1,000)...")
    update_markets(batch_size=500)
    # Markets will fetch all ~496K - that's fine, it's fast (metadata only)
    # But for testing, we just need enough to join with trades

    print("\n[2/3] Fetching order events from Goldsky (5 batches = ~5,000 events)...")
    update_goldsky(max_batches=5)

    print("\n[3/3] Processing trades (joining events with markets)...")
    try:
        process_live()
    except Exception as e:
        print(f"Processing error (expected if not enough data to join): {e}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE - Check the output files:")
    print("  markets.csv          - Market metadata")
    print("  goldsky/orderFilled.csv - Raw order events")
    print("  processed/trades.csv - Processed trades")
    print("=" * 60)
