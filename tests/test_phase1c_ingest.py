"""Phase 1-C write-chain integration tests — real PostgreSQL, no mocks.

Deterministic double-domino ingestion (fake Store Name / Domain inputs; no network).
Scraper/inference network behaviour is exercised separately by a live probe script,
not asserted here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, ObservationLog
from mes.ingest import (
    FEATURE_INFERRED_DOMAIN,
    FEATURE_OBSERVED_ON_APP_STORE,
    ingest_inferred_domain_failure,
    ingest_inferred_domain_success,
    ingest_seed,
)
from mes.normalize import normalize_domain, normalize_seed_name, seed_key


def _now() -> datetime:
    return datetime.now(UTC)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


_BATCH = "2099-01-01-01"  # sentinel year: never collides with real scheduled batches


def _unique_name() -> str:
    return f"Test Store {uuid.uuid4().hex[:8]}"


async def test_domino_one_seed_and_observed_on_app_store(session: AsyncSession) -> None:
    name = _unique_name()
    seed = await ingest_seed(session, name, batch_id=_BATCH, source_page_label="Loox Review Page")
    await session.commit()

    assert seed.entity_type == "store_name_seed"
    assert seed.canonical_key == seed_key(name)

    obs = await session.scalar(
        select(ObservationLog).where(ObservationLog.entity_id == seed.entity_id)
    )
    assert obs is not None
    assert obs.feature == FEATURE_OBSERVED_ON_APP_STORE
    assert obs.value_type == "string"
    assert obs.value_raw == name  # original Store Name 原貌
    assert obs.value_text == "Loox Review Page"
    assert obs.status == "observed"
    assert obs.confidence == "certain"
    assert obs.source == "html_page"
    assert obs.producer == "mes_crawler_v1"
    assert obs.batch_id == _BATCH


async def test_domino_two_case_a_success(session: AsyncSession) -> None:
    seed = await ingest_seed(session, _unique_name(), batch_id=_BATCH)
    store = await ingest_inferred_domain_success(
        session, seed, raw_url="https://WWW.Example-Store.com/products/",
        domain="Example-Store.com", batch_id=_BATCH,
    )
    await session.commit()

    assert store.entity_type == "store"
    assert store.canonical_key == normalize_domain("Example-Store.com")

    obs = await session.scalar(
        select(ObservationLog).where(
            ObservationLog.entity_id == seed.entity_id,
            ObservationLog.feature == FEATURE_INFERRED_DOMAIN,
        )
    )
    assert obs is not None
    assert obs.value_type == "entity_ref"
    assert obs.value_entity_id == store.entity_id
    assert obs.value_raw == "https://WWW.Example-Store.com/products/"
    assert obs.status == "observed"
    assert obs.confidence == "inferred"  # honest: domain is inferred, not certain
    assert obs.source == "web_search"  # not html_page — honest channel
    assert obs.producer == "duckduckgo_v1"
    assert obs.crawler_version is None  # crawler_version 歸位: no producer tag stashed here


@pytest.mark.parametrize("status", ["fetch_failed", "not_found"])
async def test_domino_two_case_b_failure_all_null(session: AsyncSession, status: str) -> None:
    seed = await ingest_seed(session, _unique_name(), batch_id=_BATCH)
    obs = await ingest_inferred_domain_failure(session, seed, status=status, batch_id=_BATCH)
    await session.commit()

    assert obs.feature == FEATURE_INFERRED_DOMAIN
    assert obs.status == status
    assert obs.value_type == "entity_ref"  # expected type retained
    # All value columns NULL (satisfies the failed-branch CHECK).
    assert obs.value_raw is None
    assert obs.value_text is None
    assert obs.value_number is None
    assert obs.value_boolean is None
    assert obs.value_json is None
    assert obs.value_entity_id is None
    assert obs.confidence == "inferred"
    assert obs.source == "web_search"
    assert obs.producer == "duckduckgo_v1"
    assert obs.crawler_version is None


async def test_ingest_failure_rejects_bad_status(session: AsyncSession) -> None:
    seed = await ingest_seed(session, _unique_name(), batch_id=_BATCH)
    with pytest.raises(ValueError, match="fetch_failed|not_found"):
        await ingest_inferred_domain_failure(session, seed, status="observed", batch_id=_BATCH)


async def test_seed_dedupe_same_name_one_entity(session: AsyncSession) -> None:
    name = _unique_name()
    seed1 = await ingest_seed(session, name, batch_id=_BATCH)
    await session.commit()
    seed2 = await ingest_seed(session, name.upper(), batch_id=_BATCH)  # same after normalization
    await session.commit()

    assert normalize_seed_name(name) == normalize_seed_name(name.upper())
    assert seed1.entity_id == seed2.entity_id

    count = await session.scalar(
        select(func.count())
        .select_from(Entity)
        .where(Entity.entity_type == "store_name_seed", Entity.canonical_key == seed_key(name))
    )
    assert count == 1
    # Append-Only: two sightings appended two observations onto the one Seed.
    obs_count = await session.scalar(
        select(func.count())
        .select_from(ObservationLog)
        .where(
            ObservationLog.entity_id == seed1.entity_id,
            ObservationLog.feature == FEATURE_OBSERVED_ON_APP_STORE,
        )
    )
    assert obs_count == 2
