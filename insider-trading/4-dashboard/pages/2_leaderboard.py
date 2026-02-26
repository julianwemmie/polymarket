"""Leaderboard — ranked suspicious wallets with filters."""

import streamlit as st
import pandas as pd

from lib.data import leaderboard

st.title("Suspicion Leaderboard")

# --- Filters ---
col1, col2, col3, col4 = st.columns(4)
min_score = col1.slider("Min Score", 0.0, 1.0, 0.3, 0.05)
min_metrics = col2.slider("Min Metrics Available", 1, 8, 3)
limit = col3.selectbox("Show Top", [100, 250, 500, 1000], index=1)
sort_options = {
    "Aggregate Score": "aggregate_score",
    "Timing Excess Ratio": "timing_excess_ratio",
    "Pre-spike USD": "total_pre_spike_usd",
}
sort_label = col4.selectbox("Sort By", list(sort_options.keys()))

data = leaderboard(
    limit=limit,
    min_metrics=min_metrics,
    min_score=min_score,
    sort_by=sort_options[sort_label],
)

df = pd.DataFrame(data)

if df.empty:
    st.warning("No wallets match current filters.")
    st.stop()

st.caption(f"Showing {len(df)} wallets")

# --- Format for display ---
display_cols = [
    "suspicion_rank", "wallet", "aggregate_score", "num_metrics_available",
    "timing_flagged", "timing_excess_ratio", "total_pre_spike_usd",
]
score_cols = [c for c in df.columns if c.startswith("score_")]
display_cols.extend(score_cols)

display = df[[c for c in display_cols if c in df.columns]].copy()
display.columns = [
    c.replace("score_", "").replace("_", " ").title()
    for c in display.columns
]

st.dataframe(
    display,
    hide_index=True,
    use_container_width=True,
    height=600,
    column_config={
        "Wallet": st.column_config.TextColumn(width="medium"),
        "Aggregate Score": st.column_config.ProgressColumn(
            min_value=0, max_value=1, format="%.3f"
        ),
    },
)

# --- Quick link to wallet detail ---
st.divider()
st.subheader("Inspect a Wallet")
selected = st.selectbox(
    "Select wallet to inspect",
    options=df["wallet"].tolist(),
    format_func=lambda w: f"{w[:16]}... (score: {df[df['wallet']==w]['aggregate_score'].iloc[0]:.3f})",
)
if st.button("Go to Wallet Detail"):
    st.session_state["selected_wallet"] = selected
    st.switch_page("pages/3_wallet.py")
