"""Data layer using DuckDB to query parquet files efficiently."""

from pathlib import Path

import duckdb
import streamlit as st

BASE = Path(__file__).resolve().parent.parent.parent
SIGNAL1 = BASE / "scripts" / "analysis" / "output" / "output" / "signal1"
SIGNAL2 = BASE / "scripts" / "analysis" / "output" / "output" / "signal2"
MARKETS_CSV = BASE / "historical-data" / "markets.csv"


def get_conn() -> duckdb.DuckDBPyConnection:
    """Return a per-session DuckDB connection."""
    if "duckdb_conn" not in st.session_state:
        st.session_state.duckdb_conn = duckdb.connect()
    return st.session_state.duckdb_conn


def _path(signal: int, name: str) -> str:
    base = SIGNAL1 if signal == 1 else SIGNAL2
    return str(base / name)


# ---------------------------------------------------------------------------
# Signal 1 queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600)
def overview_stats() -> dict:
    """High-level stats for the overview page."""
    conn = get_conn()
    row = conn.sql(f"""
        SELECT
            count(*) AS total_wallets,
            count(*) FILTER (aggregate_score >= 0.8) AS high_risk,
            count(*) FILTER (aggregate_score >= 0.5 AND aggregate_score < 0.8) AS medium_risk,
            avg(aggregate_score) AS mean_score,
            median(aggregate_score) AS median_score,
            max(aggregate_score) AS max_score
        FROM '{_path(1, "aggregate_scores.parquet")}'
    """).fetchone()
    return {
        "total_wallets": row[0],
        "high_risk": row[1],
        "medium_risk": row[2],
        "mean_score": row[3],
        "median_score": row[4],
        "max_score": row[5],
    }


@st.cache_data(ttl=3600)
def score_distribution(bins: int = 50) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            floor(aggregate_score * {bins}) / {bins} AS bin,
            count(*) AS count
        FROM '{_path(1, "aggregate_scores.parquet")}'
        GROUP BY 1
        ORDER BY 1
    """).fetchdf().to_dict("records")


@st.cache_data(ttl=3600)
def top_wallets(limit: int = 100, min_metrics: int = 1) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            wallet,
            aggregate_score,
            suspicion_rank,
            num_metrics_available,
            total_weight_coverage,
            score_roi,
            score_profit_factor,
            score_brier_score,
            score_contrarian_win_rate,
            score_win_streak,
            score_bet_size_vs_odds,
            score_position_concentration,
            score_niche_market_accuracy
        FROM '{_path(1, "aggregate_scores.parquet")}'
        WHERE num_metrics_available >= {min_metrics}
        ORDER BY aggregate_score DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")


@st.cache_data(ttl=3600)
def leaderboard(
    limit: int = 500,
    min_metrics: int = 1,
    min_score: float = 0.0,
    sort_by: str = "aggregate_score",
) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        WITH s1 AS (
            SELECT
                wallet,
                aggregate_score,
                suspicion_rank,
                num_metrics_available,
                score_roi,
                score_profit_factor,
                score_brier_score,
                score_contrarian_win_rate,
                score_win_streak,
                score_bet_size_vs_odds,
                score_position_concentration,
                score_niche_market_accuracy
            FROM '{_path(1, "aggregate_scores.parquet")}'
            WHERE num_metrics_available >= {min_metrics}
              AND aggregate_score >= {min_score}
        ),
        s2 AS (
            SELECT
                wallet,
                num_spikes_preceded,
                hit_rate,
                total_pre_spike_usd,
                excess_ratio,
                is_flagged AS timing_flagged
            FROM '{_path(2, "timing_scores.parquet")}'
        )
        SELECT
            s1.*,
            s2.num_spikes_preceded,
            s2.hit_rate AS timing_hit_rate,
            s2.total_pre_spike_usd,
            s2.excess_ratio AS timing_excess_ratio,
            COALESCE(s2.timing_flagged, false) AS timing_flagged
        FROM s1
        LEFT JOIN s2 ON s1.wallet = s2.wallet
        ORDER BY {sort_by} DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")


# ---------------------------------------------------------------------------
# Wallet detail queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600)
def wallet_scores(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT *
        FROM '{_path(1, "aggregate_scores.parquet")}'
        WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_roi(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(1, "roi.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_profit(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(1, "profit_factor.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_positions(wallet: str, limit: int = 200) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            market_id,
            market_question,
            side,
            total_usd_in,
            total_usd_out,
            num_trades,
            avg_entry_price,
            position_won,
            resolution,
            net_tokens,
            first_trade_timestamp,
            last_trade_timestamp
        FROM '{_path(1, "wallet_positions.parquet")}'
        WHERE wallet = '{wallet}'
        ORDER BY total_usd_in DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")


@st.cache_data(ttl=3600)
def wallet_timing(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(2, "timing_scores.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_bet_size(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(1, "bet_size_vs_odds.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_brier(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(1, "brier_score.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_concentration(wallet: str) -> dict | None:
    conn = get_conn()
    rows = conn.sql(f"""
        SELECT * FROM '{_path(1, "position_concentration.parquet")}' WHERE wallet = '{wallet}'
    """).fetchdf().to_dict("records")
    return rows[0] if rows else None


@st.cache_data(ttl=3600)
def wallet_pre_spike_trades(wallet: str, limit: int = 200) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            pst.spike_id,
            pst.entry_timestamp,
            pst.market_id,
            pst.direction,
            pst.side,
            pst.correct_direction,
            pst.usd_amount,
            pst.entry_price,
            pst.lead_time_minutes,
            ps.price_before,
            ps.price_after,
            ps.magnitude_pp
        FROM '{_path(2, "pre_spike_trades.parquet")}' pst
        JOIN '{_path(2, "price_spikes.parquet")}' ps ON pst.spike_id = ps.spike_id
        WHERE pst.wallet = '{wallet}'
        ORDER BY pst.entry_timestamp DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")


# ---------------------------------------------------------------------------
# Signal 2 queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600)
def timing_overview() -> dict:
    conn = get_conn()
    row = conn.sql(f"""
        SELECT
            count(*) AS total_wallets,
            count(*) FILTER (is_flagged) AS flagged_wallets,
            sum(total_pre_spike_usd) AS total_usd,
            avg(hit_rate) FILTER (is_flagged) AS avg_hit_rate_flagged
        FROM '{_path(2, "timing_scores.parquet")}'
    """).fetchone()
    spikes = conn.sql(f"""
        SELECT count(*) AS total_spikes,
               count(DISTINCT market_id) AS markets_with_spikes
        FROM '{_path(2, "price_spikes.parquet")}'
    """).fetchone()
    return {
        "total_wallets": row[0],
        "flagged_wallets": row[1],
        "total_pre_spike_usd": row[2],
        "avg_hit_rate_flagged": row[3],
        "total_spikes": spikes[0],
        "markets_with_spikes": spikes[1],
    }


@st.cache_data(ttl=3600)
def top_timing_wallets(limit: int = 100) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            wallet,
            num_spikes_preceded,
            num_markets,
            avg_lead_time_minutes,
            total_pre_spike_usd,
            hit_rate,
            spike_rate,
            excess_ratio,
            is_flagged
        FROM '{_path(2, "timing_scores.parquet")}'
        WHERE is_flagged = true
        ORDER BY excess_ratio DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")


@st.cache_data(ttl=3600)
def spike_magnitude_distribution() -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT
            floor(magnitude_pp * 20) / 20 AS bin,
            count(*) AS count,
            direction
        FROM '{_path(2, "price_spikes.parquet")}'
        GROUP BY 1, 3
        ORDER BY 1
    """).fetchdf().to_dict("records")


@st.cache_data(ttl=3600)
def search_wallets(query: str, limit: int = 20) -> list[dict]:
    conn = get_conn()
    return conn.sql(f"""
        SELECT wallet, aggregate_score, suspicion_rank, num_metrics_available
        FROM '{_path(1, "aggregate_scores.parquet")}'
        WHERE wallet ILIKE '%{query}%'
        ORDER BY aggregate_score DESC
        LIMIT {limit}
    """).fetchdf().to_dict("records")
