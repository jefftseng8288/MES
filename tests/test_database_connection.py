"""Integration test verifying async connectivity to a local PostgreSQL instance.

Requires PostgreSQL to be running locally (see README: `docker compose up -d`)
and reachable at MES_DATABASE_URL. This test does not use a mock database — it
opens a real async connection (asyncpg) and runs `SELECT 1`.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mes.config import get_settings


async def test_select_1_against_configured_database() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
