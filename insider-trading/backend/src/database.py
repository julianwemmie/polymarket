"""Database engine and session management.

Supports both SQLite (dev) and PostgreSQL (production) via the
DATABASE_URL setting.

Examples:
    SQLite:     sqlite+aiosqlite:///./insider_trading.db
    PostgreSQL: postgresql+asyncpg://user:pass@localhost:5432/insider_trading
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.models import Base

# Build engine kwargs based on the database backend
_engine_kwargs: dict = {"echo": False}

if settings.database_url.startswith("postgresql"):
    # PostgreSQL-specific: connection pool for concurrent access
    _engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    })

engine = create_async_engine(settings.database_url, **_engine_kwargs)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables defined on the declarative Base.

    For production use with PostgreSQL, consider using Alembic migrations
    instead. This function is kept for development convenience.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        yield session
