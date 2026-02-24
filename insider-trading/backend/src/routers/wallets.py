import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import (
    SuspicionFlag,
    SuspicionFlagResponse,
    Trade,
    TradeResponse,
    Wallet,
    WalletDetailResponse,
)

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


@router.get("/{address}", response_model=WalletDetailResponse)
async def get_wallet(
    address: str,
    db: AsyncSession = Depends(get_db),
):
    """Get wallet detail with trades and suspicion flags."""
    # Fetch wallet
    stmt = select(Wallet).where(Wallet.address == address)
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")

    # Fetch trades for this wallet, ordered by timestamp descending
    trades_stmt = (
        select(Trade)
        .where(Trade.wallet_address == address)
        .order_by(Trade.timestamp.desc())
    )
    trades_result = await db.execute(trades_stmt)
    trades = trades_result.scalars().all()

    # Fetch suspicion flags for this wallet
    flags_stmt = select(SuspicionFlag).where(SuspicionFlag.wallet_address == address)
    flags_result = await db.execute(flags_stmt)
    flags = flags_result.scalars().all()

    # Parse the JSON-encoded reasons field in each suspicion flag
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

    # Build the response
    trade_responses = [TradeResponse.model_validate(t) for t in trades]

    return WalletDetailResponse(
        address=wallet.address,
        first_seen=wallet.first_seen,
        market_count=wallet.market_count,
        total_volume=wallet.total_volume,
        total_profit=wallet.total_profit,
        suspicion_score=wallet.suspicion_score,
        funding_source=wallet.funding_source,
        trades=trade_responses,
        suspicion_flags=parsed_flags,
    )
