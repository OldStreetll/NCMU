"""Async SQLAlchemy engine + session factory.

C-IND-NEW-1 修订：Phase 1 backend uses AsyncSession everywhere — TASK-27
orchestrator must not block the event loop on Dify SSE relay. Alembic
keeps using sync psycopg in `alembic/env.py`; the runtime path is async.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ncmu_backend.config import Settings, get_settings


def _build_async_url(sync_or_neutral_url: str) -> str:
    """Coerce `postgresql://...` to `postgresql+asyncpg://...`.

    Accepts the canonical Phase 0 NCMU_DB_URL value untouched (no need
    for operators to maintain a second URL just for the async driver).
    Already-async URLs and other dialects are passed through.
    """
    if sync_or_neutral_url.startswith("postgresql://"):
        return sync_or_neutral_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_or_neutral_url.startswith("postgresql+psycopg://"):
        # Defensive: alembic-style URL — switch to asyncpg.
        return sync_or_neutral_url.replace(
            "postgresql+psycopg://", "postgresql+asyncpg://", 1
        )
    return sync_or_neutral_url


def _build_engine(settings: Settings):
    return create_async_engine(
        _build_async_url(settings.NCMU_DB_URL),
        pool_pre_ping=True,
    )


# Module-level singleton — the engine is process-wide; sessions are
# per-request via the SessionLocal factory.
_engine = _build_engine(get_settings())

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    """Tear down the engine — invoked from app lifespan shutdown / tests."""
    await _engine.dispose()
