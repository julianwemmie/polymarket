"""Timing Anomalies — Signal 2 analysis of pre-spike trading."""

import streamlit as st
import pandas as pd
import plotly.express as px

from lib.data import timing_overview, top_timing_wallets, spike_magnitude_distribution, signal2_available

st.title("Timing Anomalies (Signal 2)")
st.caption("Wallets that consistently trade before large price moves")

if not signal2_available():
    st.info("Signal 2 data not available. Run the signal 2 analysis to populate this page.")
    st.stop()

# --- Overview metrics ---
stats = timing_overview()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Price Spikes", f"{stats['total_spikes']:,}")
c2.metric("Markets with Spikes", f"{stats['markets_with_spikes']:,}")
c3.metric("Flagged Wallets", f"{stats['flagged_wallets']:,}")
c4.metric("Total Pre-spike USD", f"${stats['total_pre_spike_usd']:,.0f}")

st.divider()

# --- Spike magnitude distribution ---
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("Price Spike Magnitude Distribution")
    spike_dist = pd.DataFrame(spike_magnitude_distribution())
    if not spike_dist.empty:
        fig = px.bar(
            spike_dist,
            x="bin",
            y="count",
            color="direction",
            barmode="stack",
            labels={"bin": "Magnitude (pp)", "count": "Spikes"},
            color_discrete_map={"up": "#2ecc71", "down": "#e74c3c"},
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Key Stats")
    if stats["avg_hit_rate_flagged"]:
        st.metric("Avg Hit Rate (Flagged)", f"{stats['avg_hit_rate_flagged']:.1%}")
    st.metric("Wallets Analyzed", f"{stats['total_wallets']:,}")
    st.metric(
        "Flag Rate",
        f"{stats['flagged_wallets'] / stats['total_wallets']:.2%}" if stats["total_wallets"] > 0 else "N/A",
    )

# --- Flagged wallets table ---
st.divider()
st.subheader("Top Flagged Wallets by Excess Ratio")
st.caption("Excess ratio = how many more spikes they preceded vs expected by chance")

top = pd.DataFrame(top_timing_wallets(limit=100))
if not top.empty:
    st.dataframe(
        top,
        hide_index=True,
        use_container_width=True,
        height=500,
        column_config={
            "wallet": st.column_config.TextColumn("Wallet", width="medium"),
            "total_pre_spike_usd": st.column_config.NumberColumn("Pre-spike USD", format="$%.2f"),
            "hit_rate": st.column_config.ProgressColumn("Hit Rate", min_value=0, max_value=1, format="%.1%%"),
            "excess_ratio": st.column_config.NumberColumn("Excess Ratio", format="%.1fx"),
            "avg_lead_time_minutes": st.column_config.NumberColumn("Avg Lead (min)", format="%.0f"),
        },
    )

    # Scatter: excess ratio vs USD
    st.subheader("Excess Ratio vs Pre-Spike Volume")
    fig2 = px.scatter(
        top,
        x="excess_ratio",
        y="total_pre_spike_usd",
        size="num_spikes_preceded",
        hover_data=["wallet", "hit_rate", "num_markets"],
        labels={
            "excess_ratio": "Excess Ratio (x expected)",
            "total_pre_spike_usd": "Total Pre-spike USD",
        },
        log_y=True,
    )
    fig2.update_layout(height=500)
    st.plotly_chart(fig2, use_container_width=True)

    # Quick inspect
    st.divider()
    selected = st.selectbox(
        "Inspect a flagged wallet",
        options=top["wallet"].tolist(),
        format_func=lambda w: f"{w[:16]}... (excess: {top[top['wallet']==w]['excess_ratio'].iloc[0]:.1f}x)",
    )
    if st.button("Go to Wallet Detail"):
        st.session_state["selected_wallet"] = selected
        st.switch_page("pages/3_wallet.py")
else:
    st.info("No flagged wallets found.")
