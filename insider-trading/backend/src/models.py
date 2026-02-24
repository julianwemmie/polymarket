from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    question: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    resolution: Mapped[str] = mapped_column(String, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    open_interest: Mapped[float] = mapped_column(Float, default=0.0)
    volume_24hr: Mapped[float] = mapped_column(Float, default=0.0)
    clob_token_ids: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON array
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suspicious_wallet_count: Mapped[int] = mapped_column(Integer, default=0)
    suspicion_score: Mapped[float] = mapped_column(Float, default=0.0)

    trades: Mapped[list["Trade"]] = relationship(
        "Trade", back_populates="market", lazy="selectin"
    )
    suspicion_flags: Mapped[list["SuspicionFlag"]] = relationship(
        "SuspicionFlag", back_populates="market", lazy="selectin"
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    market_id: Mapped[str] = mapped_column(
        String, ForeignKey("markets.id"), nullable=False
    )
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)  # "BUY" / "SELL"
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # "Yes" / "No"
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False)

    market: Mapped["Market"] = relationship("Market", back_populates="trades")


class Wallet(Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    market_count: Mapped[int] = mapped_column(Integer, default=0)
    total_volume: Mapped[float] = mapped_column(Float, default=0.0)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0)
    suspicion_score: Mapped[float] = mapped_column(Float, default=0.0)
    funding_source: Mapped[str | None] = mapped_column(String, nullable=True)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)

    suspicion_flags: Mapped[list["SuspicionFlag"]] = relationship(
        "SuspicionFlag", back_populates="wallet", lazy="selectin"
    )


class SuspicionFlag(Base):
    __tablename__ = "suspicion_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(
        String, ForeignKey("wallets.address"), nullable=False
    )
    market_id: Mapped[str] = mapped_column(
        String, ForeignKey("markets.id"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[str] = mapped_column(
        String, nullable=False
    )  # JSON-encoded list of reason strings
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="suspicion_flags")
    market: Mapped["Market"] = relationship("Market", back_populates="suspicion_flags")


class MarketHolder(Base):
    __tablename__ = "market_holders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String, ForeignKey("markets.id"), nullable=False
    )
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String, ForeignKey("markets.id"), nullable=False
    )
    token_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    market_id: str
    wallet_address: str
    side: str
    outcome: str
    amount: float
    price: float
    profit: float | None = None
    timestamp: datetime
    is_suspicious: bool


class SuspicionFlagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wallet_address: str
    market_id: str
    score: float
    reasons: list[str] | str
    created_at: datetime


class MarketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question: str
    slug: str
    entity: str
    category: str
    resolution: str
    resolved_at: datetime | None = None
    created_at: datetime
    volume: float
    liquidity: float = 0.0
    open_interest: float = 0.0
    volume_24hr: float = 0.0
    suspicious_wallet_count: int
    suspicion_score: float


class MarketDetailResponse(MarketResponse):
    trades: list[TradeResponse] = []


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address: str
    first_seen: datetime | None = None
    market_count: int
    total_volume: float
    total_profit: float
    suspicion_score: float
    funding_source: str | None = None


class WalletDetailResponse(WalletResponse):
    trades: list[TradeResponse] = []
    suspicion_flags: list[SuspicionFlagResponse] = []


class LeaderboardEntry(BaseModel):
    entity: str
    total_suspicious_wallets: int
    total_markets_affected: int
    avg_suspicion_score: float
    total_suspicious_volume: float
    top_markets: list[MarketResponse]


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
