"""Funding source clustering -- hub-and-spoke detection.

Groups wallets that share a common funding source.  If 3+ wallets are
funded by the same source address and all trade in the same market, the
pattern is suspicious (typical of sybil or insider ring structures).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MIN_CLUSTER_SIZE: int = 3


@dataclass
class FundingCluster:
    """A group of wallets that share the same funding source."""

    funding_source: str
    wallets: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.wallets)


class FundingClusterDetector:
    """Detects hub-and-spoke funding patterns among wallets in a market."""

    def __init__(self, min_cluster_size: int = MIN_CLUSTER_SIZE) -> None:
        self.min_size = min_cluster_size

    def detect(
        self,
        wallet_funding: dict[str, str | None],
    ) -> list[FundingCluster]:
        """Detect funding clusters from a mapping of wallet -> funding_source.

        Parameters
        ----------
        wallet_funding:
            Mapping of wallet_address -> funding_source (or None if unknown).
            Only wallets that have traded in the current market should be
            included.

        Returns
        -------
        List of :class:`FundingCluster` where cluster size >= min_size.
        """
        # Group wallets by funding source
        source_to_wallets: dict[str, list[str]] = defaultdict(list)

        for wallet_addr, funding_src in wallet_funding.items():
            if funding_src is None or not funding_src.strip():
                continue
            source_to_wallets[funding_src.lower()].append(wallet_addr)

        clusters: list[FundingCluster] = []
        for source, wallets in source_to_wallets.items():
            if len(wallets) >= self.min_size:
                cluster = FundingCluster(
                    funding_source=source,
                    wallets=wallets,
                )
                clusters.append(cluster)

        if clusters:
            logger.info(
                "Found %d funding cluster(s): %s",
                len(clusters),
                [
                    f"{c.size} wallets funded by {c.funding_source[:10]}..."
                    for c in clusters
                ],
            )

        return clusters

    def get_clustered_wallets(
        self,
        wallet_funding: dict[str, str | None],
    ) -> dict[str, list[FundingCluster]]:
        """Return a mapping of wallet_address -> clusters it belongs to.

        Only wallets that appear in at least one cluster are included.
        """
        clusters = self.detect(wallet_funding)
        wallet_to_clusters: dict[str, list[FundingCluster]] = defaultdict(list)
        for cluster in clusters:
            for wallet in cluster.wallets:
                wallet_to_clusters[wallet].append(cluster)
        return dict(wallet_to_clusters)
