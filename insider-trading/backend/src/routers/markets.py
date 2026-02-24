from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import get_db
from src.models import Market, MarketDetailResponse, MarketResponse, Trade

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/", response_model=list[MarketResponse])
async def list_markets(
    entity: str | None = Query(None, description="Filter by entity name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List markets ordered by suspicion score, optionally filtered by entity."""
    stmt = select(Market)
    if entity:
        stmt = stmt.where(Market.entity == entity)
    stmt = stmt.order_by(Market.suspicion_score.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    markets = result.scalars().all()
    return markets


@router.get("/{market_id}", response_model=MarketDetailResponse)
async def get_market(
    market_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get market detail including all trades, ordered by timestamp."""
    stmt = (
        select(Market)
        .where(Market.id == market_id)
        .options(selectinload(Market.trades))
    )
    result = await db.execute(stmt)
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    # Sort trades by timestamp in-memory (selectinload doesn't support ordering)
    market.trades.sort(key=lambda t: t.timestamp)
    return market
