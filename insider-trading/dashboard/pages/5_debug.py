"""Debug / Exploration — run analysis on individual markets or wallets on demand via Modal."""

import streamlit as st
import polars as pl
import plotly.express as px
import plotly.graph_objects as go

from lib.data import search_wallets
from lib.modal_client import fetch_markets, remote_analyze_market, remote_analyze_wallet


@st.cache_data(ttl=3600)
def _cached_markets() -> list[dict]:
    return fetch_markets().sort("volume", descending=True, nulls_last=True).to_dicts()

st.title("Debug / Exploration")

mode = st.radio("Mode", ["Market", "Wallet"], horizontal=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_metric_table(name: str, df: pl.DataFrame):
    """Display a signal1 metric result as an expandable section."""
    if len(df) == 0:
        st.caption(f"{name}: no data")
        return
    with st.expander(f"{name} ({len(df)} wallets)", expanded=False):
        st.dataframe(df.to_pandas(), use_container_width=True)


def _metric_card(label: str, value, fmt: str = ""):
    """Render a metric value with formatting."""
    if value is None:
        return st.metric(label, "N/A")
    if fmt == "pct":
        return st.metric(label, f"{value:.1%}")
    if fmt == "usd":
        return st.metric(label, f"${value:,.0f}")
    if fmt == "f2":
        return st.metric(label, f"{value:.2f}")
    if fmt == "f4":
        return st.metric(label, f"{value:.4f}")
    return st.metric(label, str(value))


METRIC_KEYS = [
    "roi", "profit_factor", "brier_score", "contrarian",
    "niche", "concentration", "win_streak", "bet_size",
]


# ---------------------------------------------------------------------------
# MARKET MODE
# ---------------------------------------------------------------------------

if mode == "Market":
    st.subheader("Analyze a single market")

    # Load markets list from Modal (cached 1h)
    try:
        all_markets = _cached_markets()
    except Exception as e:
        st.error(f"Failed to fetch markets from Modal. Is the explore app deployed?\n\n`modal deploy modal_app/explore.py`\n\n{e}")
        st.stop()

    if not all_markets:
        st.warning("No markets found. Run the markets scraper first.")
        st.stop()

    # Text search to filter markets (avoids rendering thousands in a selectbox)
    search_query = st.text_input("Search markets", placeholder="e.g. Trump, Bitcoin, NBA...")

    if not search_query:
        st.info("Type a search term to find markets.")
        st.stop()

    query_lower = search_query.lower()
    filtered = [m for m in all_markets if query_lower in str(m.get("question", "")).lower()]

    if not filtered:
        st.warning("No markets match that search.")
        st.stop()

    # Show top 50 matches in a selectbox (fast)
    filtered = filtered[:50]
    market_options = {}
    for m in filtered:
        q = str(m.get("question", ""))
        mid = m["id"]
        vol = m.get("volume") or 0
        label = f"{q[:70]} (${vol:,.0f})" if len(q) > 70 else f"{q} (${vol:,.0f})"
        market_options[label] = m

    selected_label = st.selectbox(
        f"Select market ({len(filtered)} matches)",
        options=list(market_options.keys()),
        index=0,
    )

    market = market_options[selected_label]
    market_id = int(market["id"])

    # Market metadata
    st.divider()
    st.markdown(f"**Market:** {market['question']}")
    c1, c2 = st.columns(2)
    c1.metric("Market ID", market_id)
    vol_val = market.get("volume") or 0
    c2.metric("Volume", f"${float(vol_val):,.0f}")

    # Run analysis on submit
    if st.button("Analyze Market", type="primary"):

        with st.spinner("Running analysis on Modal (this may take a minute)..."):
            try:
                results = remote_analyze_market(market_id)
            except Exception as e:
                st.error(f"Modal analysis failed: {e}")
                st.stop()

        positions = results["positions"]

        if len(positions) == 0:
            st.warning("No trades found for this market.")
            st.stop()

        st.success(f"Loaded {len(positions):,} positions across {positions.select('wallet').n_unique():,} wallets")

        # --- Signal1 metrics (raw values) ---
        st.subheader("Signal 1: Raw Metric Values")

        roi_df = results["roi"]
        pf_df = results["profit_factor"]

        if len(roi_df) > 0:
            flagged_roi = roi_df.filter(pl.col("flagged")).height
            avg_roi = roi_df.select(pl.col("roi").mean()).item()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Wallets with ROI data", len(roi_df))
            c2.metric("Avg ROI", f"{avg_roi:.1%}" if avg_roi is not None else "N/A")
            c3.metric("ROI flagged", flagged_roi)
            if len(pf_df) > 0:
                flagged_pf = pf_df.filter(pl.col("flagged")).height
                c4.metric("PF flagged", flagged_pf)

        # Expandable sections for each metric
        for name in METRIC_KEYS:
            _display_metric_table(name, results[name])

        # --- Signal2: Timing Analysis ---
        st.subheader("Signal 2: Timing Analysis")

        price_history = results["price_history"]

        if len(price_history) == 0:
            st.info("No price history data for this market.")
        else:
            st.caption(f"Price history: {len(price_history)} time buckets")

            # Price chart
            ph_pd = price_history.to_pandas()
            fig = px.line(
                ph_pd, x="bucket_start", y="avg_price",
                title="Price History (5-min TWAP)",
                labels={"bucket_start": "Time", "avg_price": "Price"},
            )

            spikes = results["spikes"]

            if len(spikes) > 0:
                st.success(f"Detected {len(spikes)} spike(s)")

                # Add spike markers to chart
                spikes_pd = spikes.to_pandas()
                for _, spike in spikes_pd.iterrows():
                    color = "red" if spike["direction"] == "up" else "blue"
                    fig.add_vrect(
                        x0=spike["spike_start_ts"], x1=spike["spike_end_ts"],
                        fillcolor=color, opacity=0.15, line_width=0,
                        annotation_text=f"{spike['direction']} {spike['magnitude_pp']:.0%}",
                        annotation_position="top left",
                    )

                st.plotly_chart(fig, use_container_width=True)

                # Spike details table
                with st.expander(f"Spike details ({len(spikes)})", expanded=True):
                    st.dataframe(spikes_pd, use_container_width=True)

                # Pre-spike wallets
                pre_spike = results["pre_spike"]

                if len(pre_spike) > 0:
                    st.subheader("Pre-Spike Activity")
                    n_wallets = pre_spike.select("wallet").n_unique()
                    correct = pre_spike.filter(pl.col("correct_direction")).height
                    total = len(pre_spike)

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Pre-spike trades", total)
                    c2.metric("Unique wallets", n_wallets)
                    c3.metric("Correct direction", f"{correct}/{total} ({correct/total:.0%})" if total > 0 else "0")
                    c4.metric("Total USD", f"${pre_spike.select(pl.col('usd_amount').sum()).item():,.0f}")

                    with st.expander("Pre-spike trades", expanded=True):
                        st.dataframe(pre_spike.to_pandas(), use_container_width=True)
                else:
                    st.info("No pre-spike trades found for detected spikes.")
            else:
                st.plotly_chart(fig, use_container_width=True)
                st.info("No price spikes detected (threshold: 30pp in 30min window).")

        # --- Positions table ---
        st.subheader("All Positions in This Market")
        with st.expander(f"Positions ({len(positions)})", expanded=False):
            st.dataframe(
                positions.select(
                    "wallet", "side", "avg_entry_price", "total_usd_in",
                    "total_usd_out", "net_tokens", "num_trades", "position_won",
                    "resolution",
                ).sort("total_usd_in", descending=True).to_pandas(),
                use_container_width=True,
            )


# ---------------------------------------------------------------------------
# WALLET MODE
# ---------------------------------------------------------------------------

elif mode == "Wallet":
    st.subheader("Analyze a single wallet")

    default = st.session_state.get("selected_wallet", "")
    search = st.text_input("Enter wallet address (or partial)", value=default)

    if not search:
        st.info("Enter a wallet address above to inspect it.")
        st.stop()

    # Resolve wallet address (search locally — aggregate_scores.parquet exists locally)
    if len(search) < 42:
        results = search_wallets(search)
        if not results:
            st.warning("No wallets found matching that query.")
            st.stop()
        wallet_addr = st.selectbox(
            "Select wallet",
            [r["wallet"] for r in results],
            format_func=lambda w: f"{w[:16]}...",
        )
    else:
        wallet_addr = search.strip().lower()

    st.session_state["selected_wallet"] = wallet_addr
    st.caption(f"`{wallet_addr}`")

    col1, col2, _ = st.columns([1, 1, 4])
    col1.link_button("Polymarket", f"https://polymarket.com/profile/{wallet_addr}")
    col2.link_button("Polygonscan", f"https://polygonscan.com/address/{wallet_addr}")

    if st.button("Analyze Wallet", type="primary"):
        st.divider()

        with st.spinner("Running analysis on Modal..."):
            try:
                results = remote_analyze_wallet(wallet_addr)
            except Exception as e:
                st.error(f"Modal analysis failed: {e}")
                st.stop()

        positions = results["positions"]

        if len(positions) == 0:
            st.warning("No positions found for this wallet on Modal volume.")
            st.stop()

        n_markets = positions.select("market_id").n_unique()
        total_usd = positions.select(pl.col("total_usd_in").sum()).item()
        st.success(f"Loaded {len(positions):,} positions across {n_markets:,} markets (${total_usd:,.0f} total)")

        # --- Pre-computed aggregate score (if available) ---
        agg_df = results["aggregate"]
        if len(agg_df) > 0:
            st.subheader("Pre-computed Aggregate Score")
            agg = agg_df.row(0, named=True)
            c1, c2, c3, c4 = st.columns(4)
            score = agg.get("aggregate_score")
            c1.metric("Aggregate Score", f"{score:.2f}" if score is not None else "N/A")
            c2.metric("Rank", agg.get("suspicion_rank"))
            c3.metric("Metrics Available", agg.get("num_metrics_available"))
            c4.metric("Weight Coverage", f"{agg.get('total_weight_coverage', 0):.0%}")

        # --- Signal1 metrics (raw, computed on demand) ---
        st.subheader("Signal 1: Raw Metric Values (computed on demand)")

        roi_df = results["roi"]
        pf_df = results["profit_factor"]
        brier_df = results["brier_score"]
        streak_df = results["win_streak"]

        c1, c2, c3, c4 = st.columns(4)
        if len(roi_df) > 0:
            row = roi_df.row(0, named=True)
            c1.metric("ROI", f"{row['roi']:.1%}")
            c2.metric("Net Profit", f"${row['net_profit']:,.0f}")
        if len(pf_df) > 0:
            row = pf_df.row(0, named=True)
            pf_val = row["profit_factor"]
            c3.metric("Profit Factor", f"{pf_val:.1f}x" if pf_val < 999 else "INF")
        if len(brier_df) > 0:
            row = brier_df.row(0, named=True)
            c4.metric("Brier Skill vs Consensus", f"{row['brier_skill_vs_consensus']:+.2f}")

        c1, c2, c3, c4 = st.columns(4)
        if len(streak_df) > 0:
            row = streak_df.row(0, named=True)
            c1.metric("Longest Win Streak", row["longest_win_streak"])
            c2.metric("Win Rate", f"{row['win_rate']:.1%}")
        conc_df = results["concentration"]
        if len(conc_df) > 0:
            row = conc_df.row(0, named=True)
            c3.metric("Max Concentration", f"{row['max_concentration']:.1%}")
        contrarian_df = results["contrarian"]
        if len(contrarian_df) > 0:
            row = contrarian_df.row(0, named=True)
            c4.metric("Contrarian Win Rate", f"{row['contrarian_win_rate']:.1%} ({row['contrarian_bet_count']} bets)")

        # Expandable metric details
        for name in METRIC_KEYS:
            _display_metric_table(name, results[name])

        # --- Signal2: Timing ---
        st.subheader("Signal 2: Timing Analysis")

        timing_df = results["timing"]
        if len(timing_df) > 0:
            timing = timing_df.row(0, named=True)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Spikes Preceded", timing.get("num_spikes_preceded"))
            c2.metric("Hit Rate", f"{timing.get('hit_rate', 0):.1%}")
            c3.metric("Pre-spike USD", f"${timing.get('total_pre_spike_usd', 0):,.0f}")
            c4.metric("Excess Ratio", f"{timing.get('excess_ratio', 0):.1f}x")

            if timing.get("is_flagged"):
                st.error("FLAGGED for timing anomalies")

        pre_spike = results["pre_spike"]
        if len(pre_spike) > 0:
            with st.expander(f"Pre-spike trades ({len(pre_spike)})", expanded=True):
                st.dataframe(pre_spike.to_pandas(), use_container_width=True)
        elif len(timing_df) > 0:
            st.info("Timing score exists but no pre-spike trade details available.")
        else:
            st.info("No signal2 data available for this wallet. Run full pipeline first or use Market mode.")

        # --- Positions table ---
        st.subheader("Positions")
        with st.expander(f"All positions ({len(positions)})", expanded=False):
            st.dataframe(
                positions.select(
                    "market_id", "market_question", "side", "avg_entry_price",
                    "total_usd_in", "total_usd_out", "net_tokens", "num_trades",
                    "position_won", "resolution", "first_trade_timestamp",
                    "last_trade_timestamp",
                ).sort("total_usd_in", descending=True).to_pandas(),
                use_container_width=True,
            )
