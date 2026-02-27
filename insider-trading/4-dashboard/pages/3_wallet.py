"""Wallet Detail — deep dive into a specific wallet's metrics and positions."""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from lib.data import (
    wallet_scores,
    wallet_roi,
    wallet_profit,
    wallet_positions,
    wallet_timing,
    wallet_bet_size,
    wallet_brier,
    wallet_concentration,
    wallet_pre_spike_trades,
    search_wallets,
)

st.title("Wallet Detail")

# --- Wallet selection ---
default = st.session_state.get("selected_wallet", "")
search = st.text_input("Enter wallet address (or partial)", value=default)

if not search:
    st.info("Enter a wallet address above to inspect it.")
    st.stop()

# Search for matching wallets
if len(search) < 42:
    results = search_wallets(search)
    if not results:
        st.warning("No wallets found matching that query.")
        st.stop()
    wallet_addr = st.selectbox(
        "Select wallet",
        [r["wallet"] for r in results],
        format_func=lambda w: f"{w[:16]}... (score: {next(r['aggregate_score'] for r in results if r['wallet']==w):.3f})",
    )
else:
    wallet_addr = search.strip().lower()

st.session_state["selected_wallet"] = wallet_addr
st.caption(f"`{wallet_addr}`")
col_link1, col_link2, _ = st.columns([1, 1, 4])
col_link1.link_button("View on Polymarket", f"https://polymarket.com/profile/{wallet_addr}")
col_link2.link_button("View on Polygonscan", f"https://polygonscan.com/address/{wallet_addr}")

# --- Scores overview ---
scores = wallet_scores(wallet_addr)
if not scores:
    st.error("Wallet not found in aggregate scores.")
    st.stop()

st.divider()

# Top-level metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Aggregate Score", f"{scores['aggregate_score']:.4f}")
m2.metric("Suspicion Rank", f"#{scores['suspicion_rank']:,}")
m3.metric("Metrics Available", f"{scores['num_metrics_available']}/8")
m4.metric("Weight Coverage", f"{scores['total_weight_coverage']:.0%}")

# --- Radar chart of individual scores ---
st.subheader("Score Breakdown")
score_cols = {k: v for k, v in scores.items() if k.startswith("score_") and v is not None}
if score_cols:
    labels = [k.replace("score_", "").replace("_", " ").title() for k in score_cols]
    values = list(score_cols.values())

    fig = go.Figure(go.Scatterpolar(
        r=values + [values[0]],
        theta=labels + [labels[0]],
        fill="toself",
        fillcolor="rgba(255, 99, 71, 0.3)",
        line=dict(color="tomato"),
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

# --- Detailed metric panels ---
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("ROI & Profitability")
    roi = wallet_roi(wallet_addr)
    profit = wallet_profit(wallet_addr)
    if roi:
        r1, r2 = st.columns(2)
        r1.metric("ROI", f"{roi['roi']:.1%}")
        r2.metric("Net Profit", f"${roi['net_profit']:,.2f}")
        r3, r4 = st.columns(2)
        r3.metric("Total Deployed", f"${roi['total_capital_deployed']:,.2f}")
        r4.metric("Win Rate", f"{roi['win_rate']:.1%}")
        r5, r6 = st.columns(2)
        r5.metric("Resolved Bets", f"{roi['resolved_bet_count']:,}")
        r6.metric("Trading Span", f"{roi['trading_span_days']:.0f} days")
    if profit:
        st.metric("Profit Factor", f"{profit['profit_factor']:.2f}")

with col_right:
    st.subheader("Forecasting Skill")
    brier = wallet_brier(wallet_addr)
    if brier:
        b1, b2 = st.columns(2)
        b1.metric("Brier Score", f"{brier['brier_score']:.4f}")
        b2.metric("vs Consensus", f"{brier['brier_skill_vs_consensus']:.4f}")

    conc = wallet_concentration(wallet_addr)
    if conc:
        st.subheader("Position Concentration")
        cc1, cc2 = st.columns(2)
        cc1.metric("HHI", f"{conc['hhi']:.4f}")
        cc2.metric("Max Concentration", f"{conc['max_concentration']:.1%}")
        if conc.get("largest_bet_market"):
            st.caption(
                f"Largest bet: ${conc['largest_bet_usd']:,.2f} on {conc['largest_bet_side']} "
                f"(won: {conc['largest_bet_won']})"
            )

# --- Bet sizing ---
bet_size = wallet_bet_size(wallet_addr)
if bet_size:
    st.divider()
    st.subheader("Bet Size vs Odds")
    bs1, bs2, bs3, bs4 = st.columns(4)
    bs1.metric("Avg Bet Size", f"${bet_size['avg_bet_size']:,.2f}")
    bs2.metric("Total Volume", f"${bet_size['total_volume']:,.2f}")
    bs3.metric("Longshot Win Rate", f"{bet_size['longshot_win_rate']:.1%}" if bet_size['longshot_bet_count'] > 0 else "N/A")
    bs4.metric("Longshot Bets", f"{bet_size['longshot_bet_count']:,}")

    flags = []
    if bet_size.get("flagged_large_extreme"):
        flags.append("Large Extreme Bets")
    if bet_size.get("flagged_longshot_winner"):
        flags.append("Longshot Winner")
    if bet_size.get("flagged_inverse_kelly"):
        flags.append("Inverse Kelly")
    if flags:
        st.warning(f"Flags: {', '.join(flags)}")

# --- Timing analysis ---
timing = wallet_timing(wallet_addr)
if timing:
    st.divider()
    st.subheader("Timing Analysis (Signal 2)")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Spikes Preceded", f"{timing['num_spikes_preceded']:,}")
    t2.metric("Hit Rate", f"{timing['hit_rate']:.1%}")
    t3.metric("Pre-spike USD", f"${timing['total_pre_spike_usd']:,.2f}")
    t4.metric("Excess Ratio", f"{timing['excess_ratio']:.1f}x")

    if timing["is_flagged"]:
        st.error("FLAGGED for timing anomalies")

    # Show pre-spike trades
    pre_spike = wallet_pre_spike_trades(wallet_addr)
    if pre_spike:
        st.subheader("Pre-Spike Trades")
        ps_df = pd.DataFrame(pre_spike)
        st.dataframe(
            ps_df,
            hide_index=True,
            use_container_width=True,
            height=300,
            column_config={
                "usd_amount": st.column_config.NumberColumn("USD Amount", format="$%.2f"),
                "lead_time_minutes": st.column_config.NumberColumn("Lead Time (min)"),
                "magnitude_pp": st.column_config.NumberColumn("Spike Magnitude", format="%.2f"),
            },
        )

# --- Positions table ---
st.divider()
st.subheader("Positions (Top 200 by USD)")
positions = wallet_positions(wallet_addr)
if positions:
    pos_df = pd.DataFrame(positions)
    st.dataframe(
        pos_df,
        hide_index=True,
        use_container_width=True,
        height=400,
        column_config={
            "total_usd_in": st.column_config.NumberColumn("USD In", format="$%.2f"),
            "total_usd_out": st.column_config.NumberColumn("USD Out", format="$%.2f"),
            "avg_entry_price": st.column_config.NumberColumn("Avg Entry", format="%.3f"),
            "market_question": st.column_config.TextColumn("Market", width="large"),
        },
    )

    # Win/loss breakdown
    wins = sum(1 for p in positions if p.get("position_won"))
    losses = sum(1 for p in positions if p.get("position_won") is False)
    if wins + losses > 0:
        fig_wl = px.pie(
            values=[wins, losses],
            names=["Won", "Lost"],
            color_discrete_sequence=["#2ecc71", "#e74c3c"],
            title=f"Position Outcomes ({wins + losses} resolved)",
        )
        fig_wl.update_layout(height=300)
        st.plotly_chart(fig_wl, use_container_width=True)
else:
    st.info("No positions found for this wallet.")
