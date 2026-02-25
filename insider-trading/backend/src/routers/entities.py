import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import get_db
from src.models import (
    Entity,
    EntityDetailResponse,
    EntityDiscoveryMarket,
    EntityMarket,
    EntityResponse,
    EntityWalletScore,
    EntityWalletScoreResponse,
)
from src.services.entity_discovery import discover_entity_markets
from src.tasks.entity_analysis import (
    entity_progress,
    get_entity_progress,
    run_entity_analysis,
)

router = APIRouter(prefix="/api/entities", tags=["entities"])


class CreateEntityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    search_terms: list[str] = Field(default_factory=list)


class AnalyzeMarketInput(BaseModel):
    condition_id: str
    question: str
    slug: str | None = None
    volume: float = 0.0
    resolved: bool = False
    winning_outcome: str | None = None
    included: bool = True
    match_term: str | None = None
    match_terms: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    markets: list[AnalyzeMarketInput]


class DiscoverResponse(BaseModel):
    entity_id: int
    markets: list[EntityDiscoveryMarket]


@router.post("", response_model=EntityResponse)
async def create_entity(
    payload: CreateEntityRequest,
    db: AsyncSession = Depends(get_db),
):
    search_terms = [t.strip() for t in payload.search_terms if t and t.strip()]
    if not search_terms:
        raise HTTPException(status_code=400, detail="At least one search term is required")

    entity = Entity(
        name=payload.name.strip(),
        search_terms=search_terms,
        status="draft",
    )
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return entity


@router.get("", response_model=list[EntityResponse])
async def list_entities(db: AsyncSession = Depends(get_db)):
    stmt = select(Entity).order_by(Entity.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{entity_id}", response_model=EntityDetailResponse)
async def get_entity(entity_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Entity)
        .where(Entity.id == entity_id)
        .options(
            selectinload(Entity.markets),
            selectinload(Entity.wallet_scores),
        )
    )
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity.markets.sort(key=lambda m: m.volume, reverse=True)
    entity.wallet_scores.sort(
        key=lambda s: (
            s.suspicion_score is not None,
            s.suspicion_score if s.suspicion_score is not None else -1,
        ),
        reverse=True,
    )
    return entity


@router.post("/{entity_id}/discover", response_model=DiscoverResponse)
async def discover_markets_for_entity(entity_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Entity).where(Entity.id == entity_id)
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity.status = "searching"
    entity.error_message = None
    await db.commit()

    discovered = await discover_entity_markets(entity.search_terms)

    entity.discovered_market_count = len(discovered)
    entity.status = "draft"
    await db.commit()

    return DiscoverResponse(entity_id=entity_id, markets=discovered)


@router.post("/{entity_id}/analyze")
async def analyze_entity(
    entity_id: int,
    payload: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Entity).where(Entity.id == entity_id)
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    state = get_entity_progress(entity_id)
    if state.get("running"):
        raise HTTPException(status_code=409, detail="Analysis already running")

    included_markets = [m for m in payload.markets if m.included]
    if not included_markets:
        raise HTTPException(status_code=400, detail="No included markets provided")

    await db.execute(delete(EntityMarket).where(EntityMarket.entity_id == entity_id))
    await db.execute(delete(EntityWalletScore).where(EntityWalletScore.entity_id == entity_id))

    for market in payload.markets:
        selected_match_term = market.match_term
        if not selected_match_term and market.match_terms:
            selected_match_term = market.match_terms[0]

        row = EntityMarket(
            entity_id=entity_id,
            condition_id=market.condition_id,
            question=market.question,
            slug=market.slug,
            volume=market.volume,
            resolved=market.resolved,
            winning_outcome=market.winning_outcome,
            match_term=selected_match_term,
            included=market.included,
        )
        db.add(row)

    entity.status = "ingesting"
    entity.error_message = None
    entity.included_market_count = len(included_markets)
    entity.discovered_market_count = max(entity.discovered_market_count, len(payload.markets))
    entity.scored_wallet_count = 0
    entity.flagged_wallet_count = 0

    await db.commit()

    asyncio.create_task(run_entity_analysis(entity_id))

    return {
        "status": "started",
        "entity_id": entity_id,
        "markets_submitted": len(payload.markets),
        "markets_included": len(included_markets),
    }


@router.get("/{entity_id}/progress")
async def entity_progress_stream(entity_id: int):
    import json

    async def event_stream():
        while True:
            state = get_entity_progress(entity_id)
            yield f"data: {json.dumps(state)}\n\n"
            if state.get("done"):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{entity_id}/wallets", response_model=list[EntityWalletScoreResponse])
async def get_entity_wallets(
    entity_id: int,
    min_entity_markets: int = Query(default=2, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    sort: str = Query(default="suspicion"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(EntityWalletScore).where(EntityWalletScore.entity_id == entity_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    filtered = [r for r in rows if r.entity_markets_traded >= min_entity_markets]

    if sort == "delta":
        filtered.sort(
            key=lambda r: r.win_rate_delta if r.win_rate_delta is not None else -999,
            reverse=True,
        )
    elif sort == "entity_win_rate":
        filtered.sort(
            key=lambda r: r.entity_win_rate if r.entity_win_rate is not None else -999,
            reverse=True,
        )
    else:
        filtered.sort(
            key=lambda r: r.suspicion_score if r.suspicion_score is not None else -999,
            reverse=True,
        )

    return filtered[:limit]


@router.delete("/{entity_id}")
async def delete_entity(entity_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Entity).where(Entity.id == entity_id)
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    await db.delete(entity)
    await db.commit()
    entity_progress.pop(entity_id, None)

    return {"status": "deleted", "entity_id": entity_id}
