"""Entity-level aggregation of suspicion data.

Groups markets by their ``entity`` field and computes roll-up statistics
such as total suspicious wallets, number of affected markets, average
suspicion score, and total suspicious volume.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Market, SuspicionFlag, Trade


class EntityAggregator:
    """Produces entity-level leaderboard data from the database."""

    async def aggregate(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Query all markets with suspicion data, group by entity, and
        compute aggregate statistics.

        Returns a list of dicts sorted by ``total_suspicious_wallets``
        descending.  Each dict contains:

        - ``entity``
        - ``total_suspicious_wallets``
        - ``total_markets_affected``
        - ``avg_suspicion_score``
        - ``total_suspicious_volume``
        """

        # ---- Suspicious wallet counts and avg score per entity -----------
        # We join SuspicionFlag -> Market so we can group by entity.
        wallet_stats_q = (
            select(
                Market.entity,
                func.count(func.distinct(SuspicionFlag.wallet_address)).label(
                    "total_suspicious_wallets"
                ),
                func.count(func.distinct(SuspicionFlag.market_id)).label(
                    "total_markets_affected"
                ),
                func.avg(SuspicionFlag.score).label("avg_suspicion_score"),
            )
            .join(Market, SuspicionFlag.market_id == Market.id)
            .group_by(Market.entity)
        )

        wallet_stats_result = await db.execute(wallet_stats_q)
        wallet_rows = wallet_stats_result.all()

        if not wallet_rows:
            return []

        # Build a mapping: entity -> base stats
        entity_map: dict[str, dict[str, Any]] = {}
        for row in wallet_rows:
            entity_map[row.entity] = {
                "entity": row.entity,
                "total_suspicious_wallets": int(row.total_suspicious_wallets),
                "total_markets_affected": int(row.total_markets_affected),
                "avg_suspicion_score": round(float(row.avg_suspicion_score), 4),
                "total_suspicious_volume": 0.0,
            }

        # ---- Total suspicious volume per entity --------------------------
        # Sum the amounts of trades marked as suspicious, grouped by entity.
        volume_q = (
            select(
                Market.entity,
                func.sum(Trade.amount).label("total_suspicious_volume"),
            )
            .join(Market, Trade.market_id == Market.id)
            .where(Trade.is_suspicious.is_(True))
            .group_by(Market.entity)
        )

        volume_result = await db.execute(volume_q)
        for row in volume_result.all():
            if row.entity in entity_map:
                entity_map[row.entity]["total_suspicious_volume"] = round(
                    float(row.total_suspicious_volume or 0), 2
                )

        # Sort by total_suspicious_wallets descending
        entries = sorted(
            entity_map.values(),
            key=lambda e: e["total_suspicious_wallets"],
            reverse=True,
        )
        return entries

    async def get_entity_markets(
        self,
        db: AsyncSession,
        entity: str,
    ) -> list[Market]:
        """Return all ``Market`` rows whose entity matches *entity*."""
        stmt = (
            select(Market)
            .where(Market.entity == entity)
            .order_by(Market.suspicion_score.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


# Module-level singleton
entity_aggregator = EntityAggregator()
