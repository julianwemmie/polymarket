"""Temporal cluster detection for coordinated insider rings.

Finds groups of wallets placing same-direction bets on the same outcome
within a configurable time window (default 2 hours).  A cluster of 3+
wallets all buying the same outcome in a tight window is a strong signal
of coordinated trading (e.g. the Maduro case pattern).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Default parameters
CLUSTER_WINDOW_HOURS: float = 2.0
MIN_CLUSTER_SIZE: int = 3


@dataclass
class TemporalCluster:
    """A group of wallets that traded the same outcome within a time window."""

    outcome: str
    side: str
    wallets: list[str] = field(default_factory=list)
    earliest: datetime | None = None
    latest: datetime | None = None

    @property
    def size(self) -> int:
        return len(self.wallets)

    @property
    def window_hours(self) -> float:
        if self.earliest and self.latest:
            delta = (self.latest - self.earliest).total_seconds() / 3600
            return round(delta, 2)
        return 0.0


class TemporalClusterDetector:
    """Detects temporal clusters of wallets in a single market's trades."""

    def __init__(
        self,
        window_hours: float = CLUSTER_WINDOW_HOURS,
        min_cluster_size: int = MIN_CLUSTER_SIZE,
    ) -> None:
        self.window = timedelta(hours=window_hours)
        self.min_size = min_cluster_size

    def detect(
        self,
        wallet_trades: dict[str, list[dict[str, Any]]],
    ) -> list[TemporalCluster]:
        """Detect temporal clusters from per-wallet trade lists.

        Parameters
        ----------
        wallet_trades:
            Mapping of wallet_address -> list of trade dicts.
            Each trade dict must have ``side``, ``outcome``, and ``timestamp``
            (datetime).

        Returns
        -------
        List of :class:`TemporalCluster` objects where cluster size >= min_size.
        """
        # Build a flat list of (wallet, side, outcome, timestamp) sorted by time
        events: list[tuple[str, str, str, datetime]] = []

        for wallet_addr, trades in wallet_trades.items():
            for t in trades:
                side = (t.get("side", "BUY") or "BUY").upper()
                outcome = str(t.get("outcome", "")).lower()
                ts = t.get("timestamp")
                if not isinstance(ts, datetime):
                    continue
                # Normalize timezone
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                events.append((wallet_addr, side, outcome, ts))

        if not events:
            return []

        # Sort by timestamp
        events.sort(key=lambda e: e[3])

        # Group by (side, outcome) -- we look for clusters within each group
        grouped: dict[tuple[str, str], list[tuple[str, datetime]]] = defaultdict(list)
        for wallet_addr, side, outcome, ts in events:
            grouped[(side, outcome)].append((wallet_addr, ts))

        clusters: list[TemporalCluster] = []

        for (side, outcome), entries in grouped.items():
            # Sliding window approach: find groups of distinct wallets
            # within self.window of each other
            # entries is sorted by timestamp
            n = len(entries)
            i = 0
            while i < n:
                window_start = entries[i][1]
                window_end = window_start + self.window

                # Collect all distinct wallets in this window
                wallets_in_window: dict[str, datetime] = {}
                j = i
                while j < n and entries[j][1] <= window_end:
                    w_addr = entries[j][0]
                    w_ts = entries[j][1]
                    if w_addr not in wallets_in_window:
                        wallets_in_window[w_addr] = w_ts
                    j += 1

                if len(wallets_in_window) >= self.min_size:
                    timestamps = list(wallets_in_window.values())
                    cluster = TemporalCluster(
                        outcome=outcome,
                        side=side,
                        wallets=list(wallets_in_window.keys()),
                        earliest=min(timestamps),
                        latest=max(timestamps),
                    )
                    clusters.append(cluster)
                    # Skip past this window to avoid duplicate clusters
                    i = j
                else:
                    i += 1

        if clusters:
            logger.info(
                "Found %d temporal cluster(s): %s",
                len(clusters),
                [
                    f"{c.size} wallets {c.side} {c.outcome} in {c.window_hours}h"
                    for c in clusters
                ],
            )

        return clusters

    def get_clustered_wallets(
        self,
        wallet_trades: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[TemporalCluster]]:
        """Return a mapping of wallet_address -> clusters it belongs to.

        Only wallets that appear in at least one cluster are included.
        """
        clusters = self.detect(wallet_trades)
        wallet_to_clusters: dict[str, list[TemporalCluster]] = defaultdict(list)
        for cluster in clusters:
            for wallet in cluster.wallets:
                wallet_to_clusters[wallet].append(cluster)
        return dict(wallet_to_clusters)
