"""Overview page — high-level stats and score distribution."""

import streamlit as st
import plotly.express as px
import pandas as pd

from lib.data import overview_stats, score_distribution, top_wallets

st.title("Polymarket Insider Trading Detection")
st.caption("Signal 1: Statistical Implausibility | Signal 2: Timing Anomalies")

# --- Key metrics ---
stats = overview_stats()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Wallets Analyzed", f"{stats['total_wallets']:,}")
c2.metric("High Risk (>0.8)", f"{stats['high_risk']:,}")
c3.metric("Medium Risk (0.5-0.8)", f"{stats['medium_risk']:,}")
c4.metric("Max Score", f"{stats['max_score']:.4f}")

st.divider()

# --- Score distribution ---
col_chart, col_table = st.columns([3, 2])

with col_chart:
    st.subheader("Suspicion Score Distribution")
    dist = pd.DataFrame(score_distribution(bins=50))
    fig = px.bar(
        dist,
        x="bin",
        y="count",
        labels={"bin": "Aggregate Score", "count": "Wallets"},
        log_y=True,
    )
    fig.update_layout(bargap=0.05, height=400)
    st.plotly_chart(fig, use_container_width=True)

with col_table:
    st.subheader("Top 25 Suspicious Wallets")
    top = pd.DataFrame(top_wallets(limit=25, min_metrics=3))
    if not top.empty:
        display = top[["suspicion_rank", "wallet", "aggregate_score", "num_metrics_available"]].copy()
        display.columns = ["Rank", "Wallet", "Score", "Metrics"]
        display["Wallet"] = display["Wallet"].str[:12] + "..."
        st.dataframe(display, hide_index=True, use_container_width=True, height=400)
    else:
        st.info("No wallets found with 3+ metrics.")

# --- Score breakdown heatmap for top wallets ---
st.subheader("Score Breakdown — Top 50 Wallets")
top50 = pd.DataFrame(top_wallets(limit=50, min_metrics=3))
if not top50.empty:
    score_cols = [c for c in top50.columns if c.startswith("score_")]
    heatmap_df = top50[["wallet"] + score_cols].copy()
    heatmap_df["wallet"] = heatmap_df["wallet"].str[:12] + "..."
    heatmap_df = heatmap_df.set_index("wallet")
    heatmap_df.columns = [c.replace("score_", "") for c in heatmap_df.columns]

    fig2 = px.imshow(
        heatmap_df.values,
        x=heatmap_df.columns.tolist(),
        y=heatmap_df.index.tolist(),
        color_continuous_scale="RdYlGn_r",
        aspect="auto",
        labels={"color": "Score"},
    )
    fig2.update_layout(height=600)
    st.plotly_chart(fig2, use_container_width=True)
