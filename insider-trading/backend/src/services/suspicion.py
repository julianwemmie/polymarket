"""Core suspicion scoring engine.

Assigns a 0.0-1.0 suspicion score to a wallet's activity in a single
market based on seven weighted factors: wallet age, bet concentration,
trade timing (volume-weighted with escalation detection),
market-relative profit, contrarian betting, position size relative to
market volume, and win rate anomaly.

Also provides market-level statistical baselines (D2) and price spike
detection (E2).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight configuration -- seven factors, summing to 1.0
# Contrarian remains the highest single weight.
# ---------------------------------------------------------------------------

WEIGHT_WALLET_AGE: float = 0.12
WEIGHT_BET_CONCENTRATION: float = 0.08
WEIGHT_TIMING: float = 0.18
WEIGHT_PROFIT: float = 0.12
WEIGHT_CONTRARIAN: float = 0.22
WEIGHT_POSITION_SIZE: float = 0.13
WEIGHT_WIN_RATE: float = 0.15


@dataclass
class MarketStats:
    """Statistical baselines for trade amounts in a market."""

    mean: float = 0.0
    std: float = 0.0
    median: float = 0.0
    p95: float = 0.0
    count: int = 0


class SuspicionEngine:
    """Stateless scoring engine.  Call ``score_wallet`` for each
    wallet / market pair to obtain a suspicion score and human-readable
    reasons."""

    # ------------------------------------------------------------------
    # Market-level statistics (D2)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_market_stats(all_trades: list[dict[str, Any]]) -> MarketStats:
        """Compute statistical baselines across all trades in a market.

        Returns a :class:`MarketStats` dataclass with mean, std, median,
        and p95 of trade amounts.  Used by the per-wallet scorer to
        detect outlier positions via z-scores.
        """
        amounts = [abs(float(t.get("amount", 0))) for t in all_trades if float(t.get("amount", 0)) != 0]
        if not amounts:
            return MarketStats()

        mean_val = statistics.mean(amounts)
        std_val = statistics.stdev(amounts) if len(amounts) >= 2 else 0.0
        median_val = statistics.median(amounts)

        # p95
        sorted_amounts = sorted(amounts)
        p95_idx = int(len(sorted_amounts) * 0.95)
        p95_val = sorted_amounts[min(p95_idx, len(sorted_amounts) - 1)]

        return MarketStats(
            mean=mean_val,
            std=std_val,
            median=median_val,
            p95=p95_val,
            count=len(amounts),
        )

    # ------------------------------------------------------------------
    # Individual factor scorers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_wallet_age(
        market_resolved_at: datetime,
        wallet_first_seen: datetime | None,
    ) -> tuple[float, str]:
        """Score based on how recently the wallet was created relative to
        the market resolution date.

        Returns (factor_score, reason_string).
        """
        if wallet_first_seen is None:
            return 0.5, "Wallet creation date unknown (assuming moderate risk)"

        # Normalize both to UTC-aware datetimes to avoid naive/aware mismatch
        resolved = market_resolved_at
        first_seen = wallet_first_seen
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)

        delta = resolved - first_seen
        days = delta.total_seconds() / 86_400

        if days <= 7:
            score = 1.0
        elif days <= 30:
            score = 0.7
        elif days <= 90:
            score = 0.3
        else:
            score = 0.0

        return score, f"Wallet created {int(days)} days before market resolved"

    @staticmethod
    def _score_bet_concentration(wallet_market_count: int) -> tuple[float, str]:
        """Score based on how many distinct markets the wallet has traded in.

        A wallet that only participates in one or two markets is more
        suspicious than one with broad activity.
        """
        if wallet_market_count <= 1:
            score = 1.0
        elif wallet_market_count <= 3:
            score = 0.6
        elif wallet_market_count <= 10:
            score = 0.2
        else:
            score = 0.0

        return score, (
            f"Only traded in {wallet_market_count} market(s)"
            if wallet_market_count <= 3
            else f"Traded across {wallet_market_count} markets"
        )

    @staticmethod
    def _score_timing(
        trades: list[dict[str, Any]],
        market_resolved_at: datetime,
        market_end_date: datetime | None = None,
    ) -> tuple[float, str]:
        """Volume-weighted timing score across ALL trades (D3).

        Instead of only looking at the single largest trade, this version
        computes a volume-weighted average of each trade's timing score.
        It also detects escalation -- if later trades are systematically
        larger than earlier ones, a +0.15 bonus is applied.

        If the market had a known ``market_end_date`` and resolved within
        48 hours of it, trades near resolution are expected (scheduled
        event like an election) and the timing score is discounted.

        ``trades`` should be the wallet's trades in this specific market.
        Each dict must contain ``amount`` (float) and ``timestamp``
        (``datetime``).
        """
        if not trades:
            return 0.0, "No trades found for this wallet in this market"

        # Normalize timezone on resolution timestamp
        resolved = market_resolved_at
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)

        # Build a list of (timestamp, amount, timing_score) for each trade
        scored_trades: list[tuple[datetime, float, float]] = []

        for trade in trades:
            amount = abs(float(trade.get("amount", 0)))
            if amount == 0:
                continue

            trade_ts = trade.get("timestamp")
            if not isinstance(trade_ts, datetime):
                continue

            if trade_ts.tzinfo is None:
                trade_ts = trade_ts.replace(tzinfo=timezone.utc)

            delta = resolved - trade_ts
            hours = delta.total_seconds() / 3_600

            if hours <= 24:
                t_score = 1.0
            elif hours <= 72:
                t_score = 0.7
            elif hours <= 168:
                t_score = 0.4
            else:
                t_score = 0.1

            scored_trades.append((trade_ts, amount, t_score))

        if not scored_trades:
            return 0.0, "No valid trade amounts found"

        # Volume-weighted average timing score
        total_amount = sum(amt for _, amt, _ in scored_trades)
        weighted_score = sum(amt * sc for _, amt, sc in scored_trades) / total_amount

        # Escalation detection: are later trades systematically larger?
        escalation_bonus = 0.0
        if len(scored_trades) >= 2:
            # Sort by timestamp
            sorted_by_time = sorted(scored_trades, key=lambda x: x[0])
            half = len(sorted_by_time) // 2
            early_avg = statistics.mean([amt for _, amt, _ in sorted_by_time[:half]])
            late_avg = statistics.mean([amt for _, amt, _ in sorted_by_time[half:]])

            if early_avg > 0 and late_avg / early_avg >= 2.0:
                escalation_bonus = 0.15

        score = min(1.0, weighted_score + escalation_bonus)

        # Scheduled-resolution discount: if the market had a known end
        # date and resolved within 48h of it, trades near resolution are
        # normal market behaviour (e.g. elections, earnings dates).
        scheduled = False
        if market_end_date is not None:
            end_dt = market_end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            gap = abs((resolved - end_dt).total_seconds()) / 3_600
            if gap <= 48:
                scheduled = True
                score = score * 0.25  # Heavy discount for scheduled events

        # Best reason string: report the closest-to-resolution trade
        closest_trade = min(scored_trades, key=lambda x: (resolved - x[0]).total_seconds())
        closest_hours = (resolved - closest_trade[0]).total_seconds() / 3_600
        if closest_hours <= 24:
            label = f"{closest_hours:.0f} hours"
        elif closest_hours <= 168:
            label = f"{closest_hours / 24:.1f} days"
        else:
            label = f"{closest_hours / 24:.0f} days"

        reason = (
            f"Volume-weighted timing score {weighted_score:.2f} across "
            f"{len(scored_trades)} trades (closest: {label} before resolution)"
        )
        if escalation_bonus > 0:
            reason += (
                f" [ESCALATION: later trades {late_avg / early_avg:.1f}x "
                f"larger than earlier ones, +0.15 bonus]"
            )
        if scheduled:
            reason += " [SCHEDULED event — timing discounted]"

        return score, reason

    @staticmethod
    def _score_profit(
        trades: list[dict[str, Any]],
        market_volume: float,
    ) -> tuple[float, str]:
        """Score based on profit relative to market volume.

        A $500 profit on a $5k market is highly suspicious (10%);
        the same $500 on a $5M market is noise (0.01%).
        Falls back to absolute thresholds if market volume is unavailable.
        """
        total_profit = sum(float(t.get("profit", 0) or 0) for t in trades)

        if market_volume > 0:
            profit_share = total_profit / market_volume

            if profit_share > 0.10:
                score = 1.0
            elif profit_share > 0.05:
                score = 0.8
            elif profit_share > 0.02:
                score = 0.5
            elif profit_share > 0.005:
                score = 0.2
            else:
                score = 0.0

            return score, (
                f"Profit ${total_profit:,.2f} = {profit_share:.2%} of market volume"
            )

        # Fallback: absolute thresholds when volume unknown
        if total_profit > 10_000:
            score = 1.0
        elif total_profit > 5_000:
            score = 0.8
        elif total_profit > 1_000:
            score = 0.5
        elif total_profit > 100:
            score = 0.2
        else:
            score = 0.0

        return score, f"Estimated profit: ${total_profit:,.2f} (market volume unknown)"

    @staticmethod
    def _score_contrarian(
        trades: list[dict[str, Any]],
        resolution: str,
    ) -> tuple[float, str]:
        """Score based on whether the wallet bet on low-probability outcomes
        that ended up winning.

        Every documented insider case shows this pattern: buying outcomes
        priced at 5% or less that resolve in their favour.
        """
        resolved_yes = resolution.lower() in ("yes", "1")

        weighted_contrarian = 0.0
        total_amount = 0.0

        for t in trades:
            price = float(t.get("price", 0.5) or 0.5)
            amount = abs(float(t.get("amount", 0)))
            side = (t.get("side", "BUY") or "BUY").upper()
            outcome = str(t.get("outcome", "")).lower()

            if side != "BUY" or amount == 0:
                continue

            # Did this trade bet on the winning side?
            bet_on_winner = (
                (outcome in ("yes", "1") and resolved_yes)
                or (outcome in ("no", "0") and not resolved_yes)
            )

            if bet_on_winner:
                # Lower price = more contrarian = more suspicious
                # price=0.05 -> contrarian_factor=0.95
                contrarian_factor = max(0.0, 1.0 - price)
                weighted_contrarian += contrarian_factor * amount

            total_amount += amount

        if total_amount == 0:
            return 0.0, "No qualifying trades for contrarian analysis"

        avg_contrarian = weighted_contrarian / total_amount

        if avg_contrarian > 0.80:  # Avg buy price < 0.20
            score = 1.0
        elif avg_contrarian > 0.60:  # Avg buy price < 0.40
            score = 0.7
        elif avg_contrarian > 0.40:
            score = 0.3
        else:
            score = 0.0

        return score, (
            f"Contrarian score: {avg_contrarian:.2f} "
            f"(bought winning outcome at avg implied prob "
            f"{1.0 - avg_contrarian:.0%})"
        )

    @staticmethod
    def _score_position_size(
        trades: list[dict[str, Any]],
        market_volume: float,
        market_stats: MarketStats | None = None,
    ) -> tuple[float, str]:
        """Score based on total position size relative to market volume.

        Also incorporates z-score from market stats (D2): if the wallet's
        total position is >3 sigma above the mean trade amount, that is
        an additional signal.
        """
        total_position = sum(abs(float(t.get("amount", 0))) for t in trades)

        if market_volume <= 0:
            return 0.5, f"Position ${total_position:,.2f} (market volume unknown)"

        position_share = total_position / market_volume

        if position_share > 0.10:
            score = 1.0
        elif position_share > 0.05:
            score = 0.7
        elif position_share > 0.02:
            score = 0.4
        elif position_share > 0.005:
            score = 0.1
        else:
            score = 0.0

        reason = f"Position ${total_position:,.2f} = {position_share:.2%} of market volume"

        # Z-score boost from market stats (D2)
        if market_stats and market_stats.std > 0 and market_stats.count >= 5:
            z_score = (total_position - market_stats.mean) / market_stats.std
            if z_score > 3.0:
                score = min(1.0, score + 0.2)
                reason += f" [z-score: {z_score:.1f}, >3 sigma above mean]"

        return score, reason

    @staticmethod
    def _score_win_rate(win_rate: float, total_bets: int) -> tuple[float, str]:
        """Score based on win rate anomaly detection (D1).

        A sustained high win rate across multiple bets is suspicious,
        especially if the sample size is large enough to be statistically
        meaningful.
        """
        if total_bets < 2:
            return 0.0, f"Too few bets ({total_bets}) for win rate analysis"

        if win_rate >= 0.95 and total_bets >= 5:
            score = 1.0
        elif win_rate >= 0.85 and total_bets >= 4:
            score = 0.8
        elif win_rate >= 0.75 and total_bets >= 4:
            score = 0.5
        elif win_rate >= 0.65 and total_bets >= 3:
            score = 0.3
        else:
            score = 0.0

        return score, (
            f"Win rate: {win_rate:.0%} across {total_bets} bets"
        )

    @staticmethod
    def _score_price_spike(
        price_snapshots: list[dict[str, Any]],
        market_resolved_at: datetime,
    ) -> tuple[float, str]:
        """Score based on large price movements in the 48h before resolution (E2).

        Detects a sudden price spike that suggests information leakage
        before the market officially resolves.

        ``price_snapshots`` should be a list of dicts with ``timestamp``
        (datetime) and ``price`` (float), sorted by timestamp.
        """
        if not price_snapshots or len(price_snapshots) < 2:
            return 0.0, "Insufficient price data for spike analysis"

        resolved = market_resolved_at
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)

        # Get prices in the 48h window before resolution
        window_start = resolved - timedelta(hours=48)
        window_prices: list[float] = []
        pre_window_prices: list[float] = []

        for snap in price_snapshots:
            ts = snap.get("timestamp")
            if not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            price = float(snap.get("price", 0))
            if price <= 0:
                continue

            if window_start <= ts <= resolved:
                window_prices.append(price)
            elif ts < window_start:
                pre_window_prices.append(price)

        if not window_prices or not pre_window_prices:
            return 0.0, "Insufficient price data in the 48h window"

        # Compare the max price in 48h window to the average before
        pre_avg = statistics.mean(pre_window_prices)
        window_max = max(window_prices)

        if pre_avg <= 0:
            return 0.0, "Pre-window average price is zero"

        spike_ratio = window_max / pre_avg

        if spike_ratio >= 5.0:
            score = 1.0
        elif spike_ratio >= 3.0:
            score = 0.8
        elif spike_ratio >= 2.0:
            score = 0.5
        elif spike_ratio >= 1.5:
            score = 0.2
        else:
            score = 0.0

        return score, (
            f"Price spike: {spike_ratio:.1f}x increase in 48h before resolution "
            f"(pre-avg: {pre_avg:.4f}, window max: {window_max:.4f})"
        )

    # ------------------------------------------------------------------
    # Combined scorer
    # ------------------------------------------------------------------

    def score_wallet(
        self,
        wallet_address: str,
        market_id: str,
        trades: list[dict[str, Any]],
        market_resolved_at: datetime,
        wallet_first_seen: datetime | None,
        wallet_market_count: int,
        market_volume: float = 0.0,
        resolution: str = "unknown",
        win_rate: float = 0.0,
        total_bets: int = 0,
        market_stats: MarketStats | None = None,
        price_snapshots: list[dict[str, Any]] | None = None,
        market_end_date: datetime | None = None,
    ) -> tuple[float, list[str]]:
        """Compute an aggregate suspicion score for a wallet in a market.

        Parameters
        ----------
        wallet_address:
            The wallet being evaluated (used only for logging context).
        market_id:
            The market being evaluated (used only for logging context).
        trades:
            The wallet's trades in this market.  Each dict should have at
            minimum ``amount``, ``price``, ``timestamp`` (datetime),
            ``side``, ``outcome``, and optionally ``profit``.
        market_resolved_at:
            When the market resolved.
        wallet_first_seen:
            Wallet's earliest known activity date (may be ``None``).
        wallet_market_count:
            Total number of distinct markets this wallet has traded in.
        market_volume:
            Total volume of the market in USD.
        resolution:
            Market resolution outcome ("Yes", "No", etc.).
        win_rate:
            Wallet's overall win rate (0.0-1.0).
        total_bets:
            Total number of resolved bets by this wallet.
        market_stats:
            Pre-computed market statistics for z-score analysis.
        price_snapshots:
            Price history for price spike detection.

        Returns
        -------
        (score, reasons) where *score* is a float in [0.0, 1.0] and
        *reasons* is a list of human-readable explanation strings.
        """
        reasons: list[str] = []

        age_score, age_reason = self._score_wallet_age(
            market_resolved_at, wallet_first_seen
        )
        reasons.append(age_reason)

        conc_score, conc_reason = self._score_bet_concentration(wallet_market_count)
        reasons.append(conc_reason)

        timing_score, timing_reason = self._score_timing(trades, market_resolved_at, market_end_date)
        reasons.append(timing_reason)

        profit_score, profit_reason = self._score_profit(trades, market_volume)
        reasons.append(profit_reason)

        contrarian_score, contrarian_reason = self._score_contrarian(
            trades, resolution
        )
        reasons.append(contrarian_reason)

        position_score, position_reason = self._score_position_size(
            trades, market_volume, market_stats
        )
        reasons.append(position_reason)

        win_rate_score, win_rate_reason = self._score_win_rate(win_rate, total_bets)
        reasons.append(win_rate_reason)

        # Weighted average
        combined = (
            WEIGHT_WALLET_AGE * age_score
            + WEIGHT_BET_CONCENTRATION * conc_score
            + WEIGHT_TIMING * timing_score
            + WEIGHT_PROFIT * profit_score
            + WEIGHT_CONTRARIAN * contrarian_score
            + WEIGHT_POSITION_SIZE * position_score
            + WEIGHT_WIN_RATE * win_rate_score
        )

        # Price spike is an additive signal, not part of the weighted average
        # It acts as a market-level context modifier
        if price_snapshots:
            spike_score, spike_reason = self._score_price_spike(
                price_snapshots, market_resolved_at
            )
            if spike_score > 0:
                reasons.append(spike_reason)
                # Moderate boost based on spike magnitude
                combined = min(1.0, combined + spike_score * 0.10)

        # Clamp to [0, 1] (should already be in range but be safe)
        combined = max(0.0, min(1.0, combined))

        return combined, reasons


# Module-level singleton
suspicion_engine = SuspicionEngine()
