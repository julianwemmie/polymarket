"""
Modal on-demand analysis API for the dashboard debug/explore page.

Exposes three functions that run against data on the Modal volume:
  - list_markets: returns markets.csv as serialized polars DataFrame
  - analyze_market: runs full signal1 + signal2 pipeline for one market
  - analyze_wallet: loads positions + runs signal1 metrics for one wallet

Deploy (persistent, callable from dashboard):
  modal deploy modal_app/explore.py

Test locally:
  modal run modal_app/explore.py --market-id 12345
"""

import sys
import os

import modal

from modal_app.common import vol, explore_image, VOL_PATH

app = modal.App("polymarket-explore")

# Ensure pipeline code is importable inside the container
PIPELINE_PARENT = "/app"
DATA_DIR = os.path.join(VOL_PATH, "runs", "2026-02-27")


def _ensure_path():
    if PIPELINE_PARENT not in sys.path:
        sys.path.insert(0, PIPELINE_PARENT)


# ---------------------------------------------------------------------------
# list_markets
# ---------------------------------------------------------------------------


@app.function(
    image=explore_image,
    volumes={VOL_PATH: vol},
    cpu=1,
    memory=1024,
    timeout=60,
)
def list_markets() -> bytes:
    """Return markets.csv as serialized polars IPC bytes."""
    import polars as pl

    markets_path = os.path.join(DATA_DIR, "scrape", "markets.csv")
    if not os.path.exists(markets_path):
        return pl.DataFrame(schema={"id": pl.Utf8, "question": pl.Utf8}).serialize(format="binary")

    df = pl.read_csv(
        markets_path,
        schema_overrides={
            "createdAt": pl.Utf8, "id": pl.Utf8, "question": pl.Utf8,
            "answer1": pl.Utf8, "answer2": pl.Utf8, "neg_risk": pl.Utf8,
            "market_slug": pl.Utf8, "token1": pl.Utf8, "token2": pl.Utf8,
            "condition_id": pl.Utf8, "volume": pl.Float64, "ticker": pl.Utf8,
            "closedTime": pl.Utf8,
        },
    )
    return df.serialize(format="binary")


# ---------------------------------------------------------------------------
# analyze_market
# ---------------------------------------------------------------------------


@app.function(
    image=explore_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=300,
)
def analyze_market(market_id: int) -> dict[str, bytes]:
    """Run full signal1 + signal2 analysis for a single market.

    Returns dict of name -> serialized polars DataFrame (IPC binary).
    """
    _ensure_path()
    import polars as pl
    import duckdb

    from pipeline.analyze.signal1.build_positions import build_positions_for_trades
    from pipeline.analyze.signal1.roi import compute_roi
    from pipeline.analyze.signal1.profit_factor import compute_profit_factor
    from pipeline.analyze.signal1.brier_score import compute_brier_score
    from pipeline.analyze.signal1.contrarian_win_rate import compute_contrarian_win_rate
    from pipeline.analyze.signal1.niche_market_accuracy import compute_niche_market_accuracy
    from pipeline.analyze.signal1.position_concentration import compute_position_concentration
    from pipeline.analyze.signal1.win_streak import compute_win_streak
    from pipeline.analyze.signal1.bet_size_vs_odds import compute_bet_size_vs_odds
    from pipeline.analyze.signal2.build_price_history import build_price_history_for_trades
    from pipeline.analyze.signal2.detect_price_spikes import detect_spikes_for_market
    from pipeline.analyze.signal2.pre_spike_wallets import find_pre_spike_wallets

    trades_glob = os.path.join(DATA_DIR, "ingest", "trades", "*.parquet")
    markets_path = os.path.join(DATA_DIR, "scrape", "markets.csv")

    def _ser(df: pl.DataFrame) -> bytes:
        return df.serialize(format="binary")

    # Load trades for this market via DuckDB predicate pushdown
    conn = duckdb.connect()
    arrow = conn.sql(f"""
        SELECT * FROM read_parquet('{trades_glob}')
        WHERE market_id = {market_id}
    """).arrow()
    trades = pl.from_arrow(arrow)
    conn.close()

    if len(trades) == 0:
        empty = pl.DataFrame()
        return {k: _ser(empty) for k in [
            "positions", "roi", "profit_factor", "brier_score", "contrarian",
            "niche", "concentration", "win_streak", "bet_size",
            "price_history", "spikes", "pre_spike",
        ]}

    # Load markets
    markets_df = pl.read_csv(
        markets_path,
        schema_overrides={
            "createdAt": pl.Utf8, "id": pl.Utf8, "question": pl.Utf8,
            "answer1": pl.Utf8, "answer2": pl.Utf8, "neg_risk": pl.Utf8,
            "market_slug": pl.Utf8, "token1": pl.Utf8, "token2": pl.Utf8,
            "condition_id": pl.Utf8, "volume": pl.Float64, "ticker": pl.Utf8,
            "closedTime": pl.Utf8,
        },
    )

    # Signal 1: positions + 8 metrics
    print(f"Building positions for market {market_id} ({len(trades):,} trades)...")
    positions = build_positions_for_trades(trades, markets_df)

    results: dict[str, bytes] = {"positions": _ser(positions)}

    print("Computing signal1 metrics...")
    results["roi"] = _ser(compute_roi(positions))
    results["profit_factor"] = _ser(compute_profit_factor(positions))
    results["brier_score"] = _ser(compute_brier_score(positions))
    results["contrarian"] = _ser(compute_contrarian_win_rate(positions))
    results["niche"] = _ser(compute_niche_market_accuracy(positions))
    results["concentration"] = _ser(compute_position_concentration(positions))
    results["win_streak"] = _ser(compute_win_streak(positions))
    results["bet_size"] = _ser(compute_bet_size_vs_odds(positions))

    # Signal 2: price history -> spikes -> pre-spike wallets
    print("Running signal2 chain...")
    price_history = build_price_history_for_trades(trades)
    results["price_history"] = _ser(price_history)

    if len(price_history) > 0:
        spikes = detect_spikes_for_market(price_history)
        results["spikes"] = _ser(spikes)

        if len(spikes) > 0:
            pre_spike = find_pre_spike_wallets(spikes, trades)
            results["pre_spike"] = _ser(pre_spike)
        else:
            results["pre_spike"] = _ser(pl.DataFrame())
    else:
        results["spikes"] = _ser(pl.DataFrame())
        results["pre_spike"] = _ser(pl.DataFrame())

    print(f"Done — {len(positions)} positions, {len(results)} result frames")
    return results


# ---------------------------------------------------------------------------
# analyze_wallet
# ---------------------------------------------------------------------------


@app.function(
    image=explore_image,
    volumes={VOL_PATH: vol},
    cpu=4,
    memory=16384,
    timeout=300,
)
def analyze_wallet(wallet: str) -> dict[str, bytes]:
    """Load positions and run signal1 metrics for a single wallet.

    Also loads pre-computed aggregate score, timing score, and pre-spike
    trades if they exist on the volume.

    Returns dict of name -> serialized polars DataFrame (IPC binary).
    """
    _ensure_path()
    import polars as pl
    import duckdb

    from pipeline.analyze.signal1.roi import compute_roi
    from pipeline.analyze.signal1.profit_factor import compute_profit_factor
    from pipeline.analyze.signal1.brier_score import compute_brier_score
    from pipeline.analyze.signal1.contrarian_win_rate import compute_contrarian_win_rate
    from pipeline.analyze.signal1.niche_market_accuracy import compute_niche_market_accuracy
    from pipeline.analyze.signal1.position_concentration import compute_position_concentration
    from pipeline.analyze.signal1.win_streak import compute_win_streak
    from pipeline.analyze.signal1.bet_size_vs_odds import compute_bet_size_vs_odds

    def _ser(df: pl.DataFrame) -> bytes:
        return df.serialize(format="binary")

    s1_dir = os.path.join(DATA_DIR, "analyze", "signal1")
    s2_dir = os.path.join(DATA_DIR, "analyze", "signal2")
    wp_path = os.path.join(s1_dir, "wallet_positions.parquet")

    conn = duckdb.connect()

    # Load positions for this wallet
    if not os.path.exists(wp_path):
        conn.close()
        empty = pl.DataFrame()
        return {k: _ser(empty) for k in [
            "positions", "aggregate", "timing", "pre_spike",
            "roi", "profit_factor", "brier_score", "contrarian",
            "niche", "concentration", "win_streak", "bet_size",
        ]}

    arrow = conn.sql(f"""
        SELECT * FROM '{wp_path}' WHERE wallet = '{wallet}'
    """).arrow()
    positions = pl.from_arrow(arrow)

    results: dict[str, bytes] = {"positions": _ser(positions)}

    # Signal1 metrics (on-demand, raw values)
    if len(positions) > 0:
        print(f"Computing signal1 for {wallet[:12]}... ({len(positions)} positions)")
        results["roi"] = _ser(compute_roi(positions))
        results["profit_factor"] = _ser(compute_profit_factor(positions))
        results["brier_score"] = _ser(compute_brier_score(positions))
        results["contrarian"] = _ser(compute_contrarian_win_rate(positions))
        results["niche"] = _ser(compute_niche_market_accuracy(positions))
        results["concentration"] = _ser(compute_position_concentration(positions))
        results["win_streak"] = _ser(compute_win_streak(positions))
        results["bet_size"] = _ser(compute_bet_size_vs_odds(positions))
    else:
        empty = pl.DataFrame()
        for k in ["roi", "profit_factor", "brier_score", "contrarian",
                   "niche", "concentration", "win_streak", "bet_size"]:
            results[k] = _ser(empty)

    # Pre-computed aggregate score
    agg_path = os.path.join(s1_dir, "aggregate_scores.parquet")
    if os.path.exists(agg_path):
        arrow = conn.sql(f"""
            SELECT * FROM '{agg_path}' WHERE wallet = '{wallet}'
        """).arrow()
        results["aggregate"] = _ser(pl.from_arrow(arrow))
    else:
        results["aggregate"] = _ser(pl.DataFrame())

    # Pre-computed timing score
    ts_path = os.path.join(s2_dir, "timing_scores.parquet")
    if os.path.exists(ts_path):
        arrow = conn.sql(f"""
            SELECT * FROM '{ts_path}' WHERE wallet = '{wallet}'
        """).arrow()
        results["timing"] = _ser(pl.from_arrow(arrow))
    else:
        results["timing"] = _ser(pl.DataFrame())

    # Pre-spike trades
    pst_path = os.path.join(s2_dir, "pre_spike_trades.parquet")
    spk_path = os.path.join(s2_dir, "price_spikes.parquet")
    if os.path.exists(pst_path) and os.path.exists(spk_path):
        arrow = conn.sql(f"""
            SELECT
                pst.*,
                ps.price_before,
                ps.price_after,
                ps.magnitude_pp AS spike_magnitude
            FROM '{pst_path}' pst
            JOIN '{spk_path}' ps ON pst.spike_id = ps.spike_id
            WHERE pst.wallet = '{wallet}'
            ORDER BY pst.entry_timestamp DESC
        """).arrow()
        results["pre_spike"] = _ser(pl.from_arrow(arrow))
    else:
        results["pre_spike"] = _ser(pl.DataFrame())

    conn.close()
    print(f"Done — {len(positions)} positions, {len(results)} result frames")
    return results


# ---------------------------------------------------------------------------
# Local test entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(market_id: int = 0, wallet: str = ""):
    if market_id > 0:
        print(f"Testing analyze_market({market_id})...")
        results = analyze_market.remote(market_id)
        import polars as pl
        for k, v in results.items():
            df = pl.DataFrame.deserialize(v, format="binary")
            print(f"  {k}: {len(df)} rows, {df.shape[1]} cols")
    elif wallet:
        print(f"Testing analyze_wallet({wallet[:16]}...)...")
        results = analyze_wallet.remote(wallet)
        import polars as pl
        for k, v in results.items():
            df = pl.DataFrame.deserialize(v, format="binary")
            print(f"  {k}: {len(df)} rows, {df.shape[1]} cols")
    else:
        print("Testing list_markets()...")
        raw = list_markets.remote()
        import polars as pl
        df = pl.DataFrame.deserialize(raw, format="binary")
        print(f"  markets: {len(df)} rows")
        if len(df) > 0:
            print(f"  columns: {df.columns}")
            print(f"  top 3 by volume:")
            for row in df.sort("volume", descending=True).head(3).iter_rows(named=True):
                print(f"    {row['id']}: {row['question'][:60]}... vol=${row['volume']:,.0f}")
