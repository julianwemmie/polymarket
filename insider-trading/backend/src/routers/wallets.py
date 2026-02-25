import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import get_db
from src.models import (
    EntityWalletScore,
    SuspicionFlag,
    SuspicionFlagResponse,
    Trade,
    TradeResponse,
    Wallet,
    WalletDetailResponse,
    WalletEntityContext,
)
from src.services.wallet_history import fetch_wallet_full_history

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


class FullMarketRecord(BaseModel):
    """A single market's result from the wallet's full Polymarket history."""

    condition_id: str
    title: str
    outcome_bought: str
    side: str  # net side
    trades: int
    total_size: float
    total_cost: float
    resolved: bool
    won: bool | None  # None if unresolved


class WalletFullHistory(BaseModel):
    """Full trade history for a wallet from Polymarket."""

    address: str
    total_trades: int
    total_markets: int
    resolved_markets: int
    wins: int
    losses: int
    win_rate: float | None  # None if no resolved markets
    markets: list[FullMarketRecord]


@router.get("/{address}", response_model=WalletDetailResponse)
async def get_wallet(
    address: str,
    db: AsyncSession = Depends(get_db),
):
    """Get wallet detail with trades, suspicion flags, and entity context."""
    normalized_address = address.lower()

    stmt = select(Wallet).where(Wallet.address == normalized_address)
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")

    trades_stmt = (
        select(Trade)
        .where(Trade.wallet_address == normalized_address)
        .options(selectinload(Trade.market))
        .order_by(Trade.timestamp.desc())
    )
    trades_result = await db.execute(trades_stmt)
    trades = trades_result.scalars().all()

    flags_stmt = select(SuspicionFlag).where(SuspicionFlag.wallet_address == normalized_address)
    flags_result = await db.execute(flags_stmt)
    flags = flags_result.scalars().all()

    entity_scores_stmt = (
        select(EntityWalletScore)
        .where(EntityWalletScore.wallet_address == normalized_address)
        .options(selectinload(EntityWalletScore.entity))
        .order_by(EntityWalletScore.suspicion_score.desc().nullslast())
    )
    entity_scores_result = await db.execute(entity_scores_stmt)
    entity_scores = entity_scores_result.scalars().all()

    parsed_flags = []
    for flag in flags:
        flag_data = SuspicionFlagResponse(
            id=flag.id,
            wallet_address=flag.wallet_address,
            market_id=flag.market_id,
            score=flag.score,
            reasons=json.loads(flag.reasons) if isinstance(flag.reasons, str) else flag.reasons,
            created_at=flag.created_at,
        )
        parsed_flags.append(flag_data)

    trade_responses = []
    for t in trades:
        tr = TradeResponse.model_validate(t)
        if t.market:
            tr.market_question = t.market.question
            tr.market_resolution = t.market.resolution
        trade_responses.append(tr)

    entity_context = [
        WalletEntityContext(
            entity_id=s.entity_id,
            entity_name=s.entity.name if s.entity else f"Entity {s.entity_id}",
            entity_markets_traded=s.entity_markets_traded,
            entity_resolved_markets=s.entity_resolved_markets,
            entity_win_rate=s.entity_win_rate,
            overall_win_rate=s.overall_win_rate,
            win_rate_delta=s.win_rate_delta,
            suspicion_score=s.suspicion_score,
            is_flagged=s.is_flagged,
        )
        for s in entity_scores
    ]

    return WalletDetailResponse(
        address=wallet.address,
        first_seen=wallet.first_seen,
        market_count=wallet.market_count,
        total_volume=wallet.total_volume,
        total_profit=wallet.total_profit,
        suspicion_score=wallet.suspicion_score,
        funding_source=wallet.funding_source,
        win_count=wallet.win_count,
        loss_count=wallet.loss_count,
        win_rate=wallet.win_rate,
        trades=trade_responses,
        suspicion_flags=parsed_flags,
        entity_investigations=entity_context,
    )


@router.get("/{address}/full-history", response_model=WalletFullHistory)
async def get_wallet_full_history(address: str):
    """Fetch full Polymarket history and derive win/loss across all markets."""
    summary = await fetch_wallet_full_history(address.lower())

    if summary.total_trades == 0:
        raise HTTPException(status_code=404, detail="No trade history found")

    return WalletFullHistory(
        address=summary.address,
        total_trades=summary.total_trades,
        total_markets=summary.total_markets,
        resolved_markets=summary.resolved_markets,
        wins=summary.wins,
        losses=summary.losses,
        win_rate=summary.win_rate,
        markets=[
            FullMarketRecord(
                condition_id=m.condition_id,
                title=m.title,
                outcome_bought=m.outcome_bought,
                side=m.side,
                trades=m.trades,
                total_size=m.total_size,
                total_cost=m.total_cost,
                resolved=m.resolved,
                won=m.won,
            )
            for m in summary.markets
        ],
    )
