from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import (
    LeaderboardEntry,
    Market,
    MarketResponse,
    SuspicionFlag,
    Trade,
)

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@router.get("/", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    db: AsyncSession = Depends(get_db),
):
    """
    Get entity leaderboard: for each entity, count suspicious wallets,
    count markets, average suspicion score, and sum suspicious trade volume.
    Ordered by suspicious wallet count descending.
    """
    # Main aggregation query:
    # - Count distinct wallets that have suspicion flags linked to markets of that entity
    # - Count distinct markets for that entity
    # - Average market suspicion score
    # - Sum of trade amounts where is_suspicious=True
    stmt = (
        select(
            Market.entity,
            func.count(distinct(SuspicionFlag.wallet_address)).label(
                "total_suspicious_wallets"
            ),
            func.count(distinct(Market.id)).label("total_markets_affected"),
            func.avg(Market.suspicion_score).label("avg_suspicion_score"),
        )
        .outerjoin(SuspicionFlag, SuspicionFlag.market_id == Market.id)
        .group_by(Market.entity)
        .order_by(func.count(distinct(SuspicionFlag.wallet_address)).desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    # For each entity, also get the sum of suspicious trade volume
    # and the top 3 markets by suspicion score
    entries = []
    for row in rows:
        entity = row.entity

        # Sum suspicious trade volume for this entity
        vol_stmt = (
            select(func.coalesce(func.sum(Trade.amount), 0.0))
            .join(Market, Trade.market_id == Market.id)
            .where(Market.entity == entity, Trade.is_suspicious == True)  # noqa: E712
        )
        vol_result = await db.execute(vol_stmt)
        total_suspicious_volume = vol_result.scalar() or 0.0

        # Top 3 markets for this entity by suspicion score
        top_markets_stmt = (
            select(Market)
            .where(Market.entity == entity)
            .order_by(Market.suspicion_score.desc())
            .limit(3)
        )
        top_markets_result = await db.execute(top_markets_stmt)
        top_markets = top_markets_result.scalars().all()
        top_market_responses = [
            MarketResponse.model_validate(m) for m in top_markets
        ]

        entries.append(
            LeaderboardEntry(
                entity=entity,
                total_suspicious_wallets=row.total_suspicious_wallets or 0,
                total_markets_affected=row.total_markets_affected or 0,
                avg_suspicion_score=round(row.avg_suspicion_score or 0.0, 2),
                total_suspicious_volume=round(total_suspicious_volume, 2),
                top_markets=top_market_responses,
            )
        )

    return entries
