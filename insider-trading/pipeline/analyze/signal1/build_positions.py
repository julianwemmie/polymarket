# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Step 0: Build per-wallet, per-market position summary from raw trades.

Reads the 33 GB trades.csv in batches (polars read_csv_batched) and aggregates
into per-(wallet, market_id, side) positions with volume-weighted avg entry price,
total USD in (buys), total USD out (sells), net tokens, trade count, and
first/last trade timestamps.

Then joins with markets.csv to add market metadata and derives resolution outcome
from final trade prices (markets that closed have final prices near 0 or 1).

Output columns:
  wallet, market_id, side, avg_entry_price, total_usd_in, total_usd_out,
  tokens_bought, tokens_sold, net_tokens, num_trades, first_trade_timestamp,
  last_trade_timestamp, market_volume, closed_time, resolution, market_question,
  position_won

Output: output/wallet_positions.parquet
"""
import os
from pathlib import Path
import polars as pl
import time

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
TRADES_PATH = DATA_ROOT / "ingest" / "trades.csv"
MARKETS_PATH = DATA_ROOT / "scrape" / "markets.csv"
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(DATA_ROOT / "analyze" / "signal1")))
OUTPUT_PATH = OUTPUT_DIR / "wallet_positions.parquet"

BATCH_SIZE = 2_000_000


def process_batch(df: pl.DataFrame) -> pl.DataFrame:
    """
    Decompose each trade into wallet-level position entries.

    Each trade row has a maker and a taker. We create two rows:
    - One for the maker (with maker_direction)
    - One for the taker (with taker_direction)

    Both BUY and SELL trades are tracked:
    - BUY: wallet is acquiring tokens (entering position) -> adds to tokens_bought, total_usd_in
    - SELL: wallet is selling tokens (exiting position) -> adds to tokens_sold, total_usd_out

    avg_entry_price is computed as VWAP of buys only (cost basis).
    """
    # Create maker entries
    maker_df = df.select(
        pl.col("maker").alias("wallet"),
        pl.col("market_id"),
        pl.col("nonusdc_side").alias("side"),
        pl.col("maker_direction").alias("direction"),
        pl.col("price"),
        pl.col("usd_amount"),
        pl.col("token_amount"),
        pl.col("timestamp"),
        pl.col("transactionHash"),
    )

    # Create taker entries
    taker_df = df.select(
        pl.col("taker").alias("wallet"),
        pl.col("market_id"),
        pl.col("nonusdc_side").alias("side"),
        pl.col("taker_direction").alias("direction"),
        pl.col("price"),
        pl.col("usd_amount"),
        pl.col("token_amount"),
        pl.col("timestamp"),
        pl.col("transactionHash"),
    )

    # Combine maker and taker perspectives
    combined = pl.concat([maker_df, taker_df])

    # Deduplicate neg-risk double-counts: in neg-risk markets, a single economic
    # action creates two OrderFilled events in the same transaction — one where
    # the wallet trades with the NegRiskAdapter and one with the actual
    # counterparty. The wallet appears in both with the same market/side/direction,
    # inflating volumes. Keep only one entry per (tx, wallet, market, side, direction).
    combined = combined.unique(
        subset=["transactionHash", "wallet", "market_id", "side", "direction"],
        keep="first",
    )

    # Aggregate per (wallet, market_id, side), tracking buys and sells separately.
    #
    # NOTE on granularity: Each (wallet, market_id, side) tuple is a separate
    # position. A wallet that holds BOTH token1 and token2 in the same market
    # will have TWO rows. This is correct per-position behavior (each token
    # side IS a separate position with its own entry price and P&L), but
    # downstream scripts computing per-wallet stats (win rates, bet counts,
    # streaks) should be aware that the same market_id may appear twice for
    # one wallet. Deduplication by (market_id, wallet) may be needed depending
    # on the analysis.
    agg = combined.group_by(["wallet", "market_id", "side"]).agg(
        # Buy-side aggregation for cost basis (VWAP = total_usd_in / tokens_bought)
        pl.col("usd_amount").filter(pl.col("direction") == "BUY").sum().alias("total_usd_in"),
        pl.col("token_amount").filter(pl.col("direction") == "BUY").sum().alias("tokens_bought"),
        # Sell-side aggregation
        pl.col("usd_amount").filter(pl.col("direction") == "SELL").sum().alias("total_usd_out"),
        pl.col("token_amount").filter(pl.col("direction") == "SELL").sum().alias("tokens_sold"),
        # Overall trade stats
        pl.col("timestamp").count().alias("num_trades"),
        pl.col("timestamp").min().alias("first_trade_timestamp"),
        pl.col("timestamp").max().alias("last_trade_timestamp"),
    )

    # Fill nulls from filtered sums (e.g., wallet only has buys -> sells are null)
    agg = agg.with_columns(
        pl.col("total_usd_in").fill_null(0.0),
        pl.col("tokens_bought").fill_null(0.0),
        pl.col("total_usd_out").fill_null(0.0),
        pl.col("tokens_sold").fill_null(0.0),
    )

    return agg


def derive_resolution(markets: pl.DataFrame, last_prices: pl.DataFrame) -> pl.DataFrame:
    """
    Derive resolution outcome for closed markets from final trade prices.

    Logic: For closed markets, look at the last trade price for token1.
    - If last price >= 0.85 -> token1 won (resolution = "token1")
    - If last price <= 0.15 -> token2 won (resolution = "token2")
    - Otherwise -> ambiguous / unresolved
    """
    # Join last prices onto markets
    markets_with_prices = markets.join(
        last_prices, left_on="id", right_on="market_id", how="left"
    )

    # Derive resolution
    markets_with_resolution = markets_with_prices.with_columns(
        pl.when(pl.col("closedTime").is_not_null() & pl.col("last_price").is_not_null())
        .then(
            pl.when(pl.col("last_price") >= 0.85)
            .then(pl.lit("token1"))
            .when(pl.col("last_price") <= 0.15)
            .then(pl.lit("token2"))
            .otherwise(pl.lit("ambiguous"))
        )
        .otherwise(pl.lit("unresolved"))
        .alias("resolution")
    )

    return markets_with_resolution


def main():
    print("=" * 60)
    print("Building wallet positions from trades.csv")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    trades_path = TRADES_PATH.resolve()
    markets_path = MARKETS_PATH.resolve()

    print(f"Trades file: {trades_path}")
    print(f"Markets file: {markets_path}")

    if not trades_path.exists():
        raise FileNotFoundError(f"Trades file not found: {trades_path}")
    if not markets_path.exists():
        raise FileNotFoundError(f"Markets file not found: {markets_path}")

    # ---- Phase 1: Process trades in batches ----
    print(f"\nPhase 1: Reading trades in batches of {BATCH_SIZE:,} rows...")
    start = time.time()

    reader = pl.read_csv_batched(
        trades_path,
        schema_overrides={
            "timestamp": pl.Utf8,
            "market_id": pl.Utf8,
            "maker": pl.Utf8,
            "taker": pl.Utf8,
            "nonusdc_side": pl.Utf8,
            "maker_direction": pl.Utf8,
            "taker_direction": pl.Utf8,
            "price": pl.Float64,
            "usd_amount": pl.Float64,
            "token_amount": pl.Float64,
            "transactionHash": pl.Utf8,
        },
        batch_size=BATCH_SIZE,
    )

    partial_aggs = []
    # Also collect last trade price per (market_id, side=token1) for resolution derivation
    last_price_records = []
    total_rows = 0
    batch_num = 0

    while True:
        batches = reader.next_batches(1)
        if batches is None or len(batches) == 0:
            break
        batch = batches[0]
        batch_num += 1
        total_rows += len(batch)
        print(f"  Batch {batch_num}: {total_rows:,} rows processed", flush=True)

        # Get partial aggregation for this batch
        agg = process_batch(batch)
        partial_aggs.append(agg)

        # Track last trade prices for token1 per market (for resolution derivation)
        token1_trades = batch.filter(pl.col("nonusdc_side") == "token1")
        if len(token1_trades) > 0:
            # Use sort + unique(keep="last") instead of group_by to preserve sort order
            last_prices_batch = (
                token1_trades
                .sort("timestamp")
                .unique(subset=["market_id"], keep="last")
                .select(
                    pl.col("market_id"),
                    pl.col("price").alias("last_price"),
                    pl.col("timestamp").alias("last_trade_ts"),
                )
            )
            last_price_records.append(last_prices_batch)

    elapsed = time.time() - start
    print(f"\nPhase 1 complete: {total_rows:,} rows in {elapsed:.1f}s")

    # ---- Phase 2: Merge partial aggregations ----
    print("\nPhase 2: Merging partial aggregations...")
    start = time.time()

    # Concatenate all partial aggs.
    # NOTE: Memory safety — each partial agg is already compressed to unique
    # (wallet, market_id, side) tuples per batch. The concat may be large but is
    # bounded by (num_unique_positions * num_batches) and the re-aggregation below
    # immediately reduces it back to unique positions.
    all_aggs = pl.concat(partial_aggs)

    # Re-aggregate: sum the numeric columns, min/max timestamps
    positions = all_aggs.group_by(["wallet", "market_id", "side"]).agg(
        pl.col("total_usd_in").sum().alias("total_usd_in"),
        pl.col("tokens_bought").sum().alias("tokens_bought"),
        pl.col("total_usd_out").sum().alias("total_usd_out"),
        pl.col("tokens_sold").sum().alias("tokens_sold"),
        pl.col("num_trades").sum().alias("num_trades"),
        pl.col("first_trade_timestamp").min().alias("first_trade_timestamp"),
        pl.col("last_trade_timestamp").max().alias("last_trade_timestamp"),
    )

    # Compute net_tokens (bought - sold, floored at 0) and avg_entry_price (VWAP of buys)
    positions = positions.with_columns(
        # net_tokens: tokens still held at resolution, cannot be negative
        pl.max_horizontal(pl.col("tokens_bought") - pl.col("tokens_sold"), pl.lit(0.0)).alias("net_tokens"),
        # avg_entry_price: VWAP of buys = total_usd_in / tokens_bought (true cost basis).
        # Previously this was computed as sum(price * usd_amount) / sum(usd_amount), which
        # is price^2-weighted, not volume-weighted. The correct VWAP is simply total cost
        # divided by total tokens acquired.
        pl.when(pl.col("tokens_bought") > 0)
        .then(pl.col("total_usd_in") / pl.col("tokens_bought"))
        .otherwise(pl.lit(None))
        .alias("avg_entry_price"),
    )

    elapsed = time.time() - start
    print(f"Phase 2 complete: {len(positions):,} positions in {elapsed:.1f}s")

    # ---- Phase 3: Derive resolution from last trade prices ----
    print("\nPhase 3: Deriving market resolutions...")
    start = time.time()

    # Merge all last-price records and keep the actual last one per market
    all_last_prices = pl.concat(last_price_records)
    # Use sort + unique(keep="last") to guarantee order-preserving last-price selection
    final_last_prices = (
        all_last_prices
        .sort("last_trade_ts")
        .unique(subset=["market_id"], keep="last")
        .select("market_id", "last_price", "last_trade_ts")
    )

    # ---- Phase 4: Load markets and join ----
    print("\nPhase 4: Loading markets and joining...")
    markets = pl.read_csv(
        markets_path,
        schema_overrides={
            "createdAt": pl.Utf8,
            "id": pl.Utf8,
            "question": pl.Utf8,
            "answer1": pl.Utf8,
            "answer2": pl.Utf8,
            "neg_risk": pl.Utf8,
            "market_slug": pl.Utf8,
            "token1": pl.Utf8,
            "token2": pl.Utf8,
            "condition_id": pl.Utf8,
            "volume": pl.Float64,
            "ticker": pl.Utf8,
            "closedTime": pl.Utf8,
        },
    )

    # Derive resolution
    markets_resolved = derive_resolution(markets, final_last_prices)

    # Select relevant market columns for join
    market_info = markets_resolved.select(
        pl.col("id").alias("market_id"),
        pl.col("volume").alias("market_volume"),
        pl.col("closedTime").alias("closed_time"),
        pl.col("resolution"),
        pl.col("question").alias("market_question"),
    )

    # Join positions with market info
    positions_final = positions.join(market_info, on="market_id", how="left")

    # Add a boolean for whether this position won.
    # Explicitly handle nulls: unresolved/ambiguous markets -> position_won = False
    positions_final = positions_final.with_columns(
        pl.when(pl.col("resolution").is_in(["token1", "token2"]) & (pl.col("resolution") == pl.col("side")))
        .then(pl.lit(True))
        .otherwise(pl.lit(False))
        .alias("position_won"),
    )

    elapsed = time.time() - start
    print(f"Phase 4 complete in {elapsed:.1f}s")

    # ---- Phase 5: Write output ----
    print(f"\nWriting {len(positions_final):,} positions to {OUTPUT_PATH}")
    positions_final.write_parquet(OUTPUT_PATH)

    # Print summary stats
    n_wallets = positions_final.select("wallet").n_unique()
    n_markets = positions_final.select("market_id").n_unique()
    n_resolved = positions_final.filter(
        pl.col("resolution").is_in(["token1", "token2"])
    ).select("market_id").n_unique()

    print(f"\nSummary:")
    print(f"  Total positions: {len(positions_final):,}")
    print(f"  Unique wallets:  {n_wallets:,}")
    print(f"  Unique markets:  {n_markets:,}")
    print(f"  Resolved markets: {n_resolved:,}")
    print(f"  Output: {OUTPUT_PATH}")
    print("Done!")


if __name__ == "__main__":
    main()
