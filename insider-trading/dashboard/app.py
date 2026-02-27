"""Polymarket Insider Trading Dashboard — main entry point."""

import streamlit as st

st.set_page_config(
    page_title="Polymarket Insider Trading Detector",
    page_icon="\U0001f575",
    layout="wide",
    initial_sidebar_state="expanded",
)

overview = st.Page("pages/1_overview.py", title="Overview", icon="\U0001f4ca", default=True)
leaderboard = st.Page("pages/2_leaderboard.py", title="Leaderboard", icon="\U0001f3c6")
wallet = st.Page("pages/3_wallet.py", title="Wallet Detail", icon="\U0001f50d")
timing = st.Page("pages/4_timing.py", title="Timing Anomalies", icon="\u23f0")

pg = st.navigation([overview, leaderboard, wallet, timing])
pg.run()
