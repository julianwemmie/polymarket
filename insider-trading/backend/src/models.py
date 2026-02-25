from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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

    overall_wins_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_losses_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_win_rate_cached: Mapped[float | None] = mapped_column(Float, nullable=True)
    full_history_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    suspicion_flags: Mapped[list["SuspicionFlag"]] = relationship(
        "SuspicionFlag", back_populates="wallet", lazy="selectin"
    )
    entity_scores: Mapped[list["EntityWalletScore"]] = relationship(
        "EntityWalletScore", back_populates="wallet", lazy="selectin"
    )


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    search_terms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, default="draft", nullable=False)
    discovered_market_count: Mapped[int] = mapped_column(Integer, default=0)
    included_market_count: Mapped[int] = mapped_column(Integer, default=0)
    scored_wallet_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_wallet_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    markets: Mapped[list["EntityMarket"]] = relationship(
        "EntityMarket", back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )
    wallet_scores: Mapped[list["EntityWalletScore"]] = relationship(
        "EntityWalletScore", back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )


class EntityMarket(Base):
    __tablename__ = "entity_markets"
    __table_args__ = (
        UniqueConstraint("entity_id", "condition_id", name="uq_entity_market_condition"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    condition_id: Mapped[str] = mapped_column(String, nullable=False)
    question: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str | None] = mapped_column(String, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    winning_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    match_term: Mapped[str | None] = mapped_column(String, nullable=True)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    entity: Mapped["Entity"] = relationship("Entity", back_populates="markets")


class EntityWalletScore(Base):
    __tablename__ = "entity_wallet_scores"
    __table_args__ = (
        UniqueConstraint("entity_id", "wallet_address", name="uq_entity_wallet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    wallet_address: Mapped[str] = mapped_column(
        String, ForeignKey("wallets.address"), nullable=False
    )

    entity_markets_traded: Mapped[int] = mapped_column(Integer, default=0)
    entity_resolved_markets: Mapped[int] = mapped_column(Integer, default=0)
    entity_wins: Mapped[int] = mapped_column(Integer, default=0)
    entity_losses: Mapped[int] = mapped_column(Integer, default=0)
    entity_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    entity_profit: Mapped[float] = mapped_column(Float, default=0.0)

    overall_markets: Mapped[int] = mapped_column(Integer, default=0)
    overall_wins: Mapped[int] = mapped_column(Integer, default=0)
    overall_losses: Mapped[int] = mapped_column(Integer, default=0)
    overall_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    win_rate_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    suspicion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    market_breakdown: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    entity: Mapped["Entity"] = relationship("Entity", back_populates="wallet_scores")
    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="entity_scores")


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
    market_question: str | None = None
    market_resolution: str | None = None


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
    is_active: bool = False
    suspicious_wallet_count: int
    suspicion_score: float


class MarketDetailResponse(MarketResponse):
    trades: list[TradeResponse] = []


class WalletEntityContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: int
    entity_name: str
    entity_markets_traded: int
    entity_resolved_markets: int
    entity_win_rate: float | None = None
    overall_win_rate: float | None = None
    win_rate_delta: float | None = None
    suspicion_score: float | None = None
    is_flagged: bool = False


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address: str
    first_seen: datetime | None = None
    market_count: int
    total_volume: float
    total_profit: float
    suspicion_score: float
    funding_source: str | None = None
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0


class WalletDetailResponse(WalletResponse):
    trades: list[TradeResponse] = []
    suspicion_flags: list[SuspicionFlagResponse] = []
    entity_investigations: list[WalletEntityContext] = []


class WalletLeaderboardEntry(BaseModel):
    address: str
    win_rate: float
    win_count: int
    loss_count: int
    total_bets: int
    total_profit: float
    total_volume: float
    market_count: int
    suspicion_score: float


class EntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    search_terms: list[str]
    status: str
    discovered_market_count: int
    included_market_count: int
    scored_wallet_count: int
    flagged_wallet_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class EntityMarketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    condition_id: str
    question: str
    slug: str | None = None
    volume: float
    resolved: bool
    winning_outcome: str | None = None
    match_term: str | None = None
    included: bool
    created_at: datetime


class EntityWalletScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    wallet_address: str

    entity_markets_traded: int
    entity_resolved_markets: int
    entity_wins: int
    entity_losses: int
    entity_win_rate: float | None = None
    entity_profit: float

    overall_markets: int
    overall_wins: int
    overall_losses: int
    overall_win_rate: float | None = None

    win_rate_delta: float | None = None
    suspicion_score: float | None = None
    is_flagged: bool = False
    reasons: list[str] = []
    market_breakdown: list[dict] | None = None
    created_at: datetime


class EntityDetailResponse(EntityResponse):
    markets: list[EntityMarketResponse] = []
    wallet_scores: list[EntityWalletScoreResponse] = []


class EntityDiscoveryMarket(BaseModel):
    condition_id: str
    question: str
    slug: str | None = None
    volume: float = 0.0
    resolved: bool = False
    winning_outcome: str | None = None
    match_terms: list[str] = []
    included: bool = True
