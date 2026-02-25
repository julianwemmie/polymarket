"""Win-rate-primary suspicion scoring engine.

Assigns a 0.0-1.0 suspicion score to a wallet based primarily on its
win rate and total number of markets. Informational reasons are added for
profit magnitude, contrarian markets, and large position sizes.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MarketStats:
    """Statistical baselines for trade amounts in a market."""

    mean: float = 0.0
    std: float = 0.0
    median: float = 0.0
    p95: float = 0.0
    count: int = 0


class SuspicionEngine:
    """Win-rate-primary scoring engine.

    Call ``score_wallet`` for each wallet / market pair to obtain a
    suspicion score and human-readable reasons.
    """

    # ------------------------------------------------------------------
    # Market-level statistics
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_market_stats(all_trades: list[dict[str, Any]]) -> MarketStats:
        """Compute statistical baselines across all trades in a market."""
        amounts = [abs(float(t.get("amount", 0))) for t in all_trades if float(t.get("amount", 0)) != 0]
        if not amounts:
            return MarketStats()

        mean_val = statistics.mean(amounts)
        std_val = statistics.stdev(amounts) if len(amounts) >= 2 else 0.0
        median_val = statistics.median(amounts)

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
    # Core scorer
    # ------------------------------------------------------------------

    def score_wallet(
        self,
        wallet_address: str,
        market_id: str,
        trades: list[dict[str, Any]],
        market_volume: float,
        resolution: str,
        win_rate: float,
        total_markets: int,
    ) -> tuple[float, list[str]]:
        """Compute a suspicion score primarily from win_rate + total_markets.

        Parameters
        ----------
        wallet_address:
            The wallet being evaluated (for logging context).
        market_id:
            The market being evaluated (for logging context).
        trades:
            The wallet's trades in this market.
        market_volume:
            Total volume of the market in USD.
        resolution:
            Market resolution outcome ("Yes", "No", etc.).
        win_rate:
            Wallet's overall win rate (0.0-1.0).
        total_markets:
            Total number of resolved markets by this wallet.

        Returns
        -------
        (score, reasons) where *score* is a float in [0.0, 1.0] and
        *reasons* is a list of human-readable explanation strings.
        """
        reasons: list[str] = []

        # ---- Primary signal: win rate + total markets ----
        # Need a meaningful sample size AND high win rate to flag.
        # 60-70% across a handful of markets is normal for skilled traders.
        # Note: win rate reason is NOT added here — the wallet page header
        # shows the real-time win rate. Per-market flag reasons below focus
        # on what happened in THIS specific market.
        if total_markets < 5:
            score = 0.0
        elif win_rate >= 0.90 and total_markets >= 10:
            score = 1.0
        elif win_rate >= 0.90 and total_markets >= 5:
            score = 0.85
        elif win_rate >= 0.80 and total_markets >= 10:
            score = 0.75
        elif win_rate >= 0.80 and total_markets >= 5:
            score = 0.6
        elif win_rate >= 0.70 and total_markets >= 10:
            score = 0.4
        else:
            score = 0.0

        # ---- Informational: profit magnitude ----
        total_profit = sum(float(t.get("profit", 0) or 0) for t in trades)
        if market_volume > 0:
            profit_share = total_profit / market_volume
            reasons.append(
                f"Profit ${total_profit:,.2f} = {profit_share:.2%} of market volume"
            )
        elif total_profit != 0:
            reasons.append(f"Estimated profit: ${total_profit:,.2f}")

        # ---- Informational: contrarian markets ----
        resolved_yes = resolution.lower() in ("yes", "1")
        contrarian_buys = []
        for t in trades:
            price = float(t.get("price", 0.5) or 0.5)
            amount = abs(float(t.get("amount", 0)))
            side = (t.get("side", "BUY") or "BUY").upper()
            outcome = str(t.get("outcome", "")).lower()

            if side != "BUY" or amount == 0:
                continue

            bet_on_winner = (
                (outcome in ("yes", "1") and resolved_yes)
                or (outcome in ("no", "0") and not resolved_yes)
            )

            if bet_on_winner and price < 0.30:
                contrarian_buys.append(price)

        if contrarian_buys:
            avg_price = statistics.mean(contrarian_buys)
            reasons.append(
                f"Contrarian: {len(contrarian_buys)} winning buy(s) at avg price "
                f"{avg_price:.0%} (low-probability outcome)"
            )

        # ---- Informational: large position size ----
        total_position = sum(abs(float(t.get("amount", 0))) for t in trades)
        if market_volume > 0:
            position_share = total_position / market_volume
            if position_share > 0.02:
                reasons.append(
                    f"Large position: ${total_position:,.2f} = {position_share:.2%} of market volume"
                )

        return score, reasons


# Module-level singleton
suspicion_engine = SuspicionEngine()
