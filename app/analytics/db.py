"""
Async SQLAlchemy engine + session factory.
Call init_db() once at startup to create all tables.
Use get_session() as an async context manager in tracker/query code.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analytics.models import Base
from app.core.config import settings

_engine = None
_session_factory: async_sessionmaker | None = None


def _get_engine():
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set in .env")
        _engine = create_async_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
    return _engine


async def init_db():
    """Create all tables if they don't exist. Call once on startup."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Analytics DB tables verified/created")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
