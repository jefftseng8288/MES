"""Async engine and session factory for MES database access."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mes.config import get_settings


def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url)


engine = get_engine()
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        await session.close()


async def check_connection() -> bool:
    """Run ``SELECT 1`` against the configured database and return whether it succeeded."""
    async with get_session() as session:
        result = await session.execute(text("SELECT 1"))
        value: int = result.scalar_one()
        return value == 1
