"""Phase 1 harvest scheduling layer tests.

Health-report math is tested purely (no DB/network). The batch report is tested
against a small known batch inserted through the real chain, keyed by batch_id.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.ingest import ingest_inferred_domain_failure, ingest_inferred_domain_success, ingest_seed
from mes.pipeline import HealthReport, _resolve_batch_id, compute_health_for_batch

_BATCH = "2026-07-15-01"


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def test_three_proportions_computed_separately() -> None:
    # 6 observed, 3 not_found, 1 fetch_failed out of 10.
    statuses = ["observed"] * 6 + ["not_found"] * 3 + ["fetch_failed"]
    r = HealthReport.from_statuses(_BATCH, requested=30, statuses=statuses)
    assert r.actual == 10
    assert (r.observed, r.not_found, r.fetch_failed) == (6, 3, 1)
    assert r._pct(r.observed) == "60%"
    assert r._pct(r.not_found) == "30%"
    assert r._pct(r.fetch_failed) == "10%"


def test_report_shows_batch_id_and_never_merges_into_success_rate() -> None:
    statuses = ["observed", "not_found", "fetch_failed"]
    text = HealthReport.from_statuses(_BATCH, 3, statuses).format()
    assert _BATCH in text  # report is keyed by batch_id
    # Each status has its own proportion line (three separate percentages).
    for label in ("observed", "not_found", "fetch_failed"):
        assert f"{label}" in text
    assert text.count("33%") == 3  # each of the 3 statuses = 1/3, shown separately
    assert "主儀表" in text  # fetch_failed flagged as the adjustment dial
    # The only mention of "成功率" is the header stating it is NOT merged into one.
    assert "不合併成單一「成功率」" in text


def test_shortfall_flagged_when_actual_below_requested() -> None:
    r = HealthReport.from_statuses(_BATCH, requested=30, statuses=["observed"])
    assert r.actual < r.requested
    assert "供給不足" in r.format()


def test_empty_batch_pct_is_dash() -> None:
    r = HealthReport.from_statuses(_BATCH, requested=30, statuses=[])
    assert r._pct(0) == "—"


async def test_batch_id_scheduled_slot_is_fixed(session: AsyncSession) -> None:
    # A given slot always maps to the same -0N, regardless of DB contents.
    assert await _resolve_batch_id(session, "2099-02-02", 1) == "2099-02-02-01"
    assert await _resolve_batch_id(session, "2099-02-02", 2) == "2099-02-02-02"
    assert await _resolve_batch_id(session, "2099-02-02", 3) == "2099-02-02-03"


async def test_batch_id_manual_starts_at_04(session: AsyncSession) -> None:
    # Manual (slot=None) reserves 1~3 for scheduled slots, so first manual = -04.
    assert await _resolve_batch_id(session, "2099-02-03", None) == "2099-02-03-04"


async def test_compute_health_for_batch_counts_by_batch_id(session: AsyncSession) -> None:
    # Use a batch_id unlikely to collide; delta before/after handles repeat runs.
    batch = "2099-01-01-01"
    before = await compute_health_for_batch(session, batch)

    s1 = await ingest_seed(session, f"Harvest Obs {uuid.uuid4().hex[:6]}", batch_id=batch)
    await ingest_inferred_domain_success(
        session, s1, raw_url="https://ex-obs.com/", domain="ex-obs.com", batch_id=batch
    )
    s2 = await ingest_seed(session, f"Harvest NF {uuid.uuid4().hex[:6]}", batch_id=batch)
    await ingest_inferred_domain_failure(session, s2, status="not_found", batch_id=batch)
    s3 = await ingest_seed(session, f"Harvest FF {uuid.uuid4().hex[:6]}", batch_id=batch)
    await ingest_inferred_domain_failure(session, s3, status="fetch_failed", batch_id=batch)
    await session.commit()

    after = await compute_health_for_batch(session, batch)
    assert after.observed - before.observed == 1
    assert after.not_found - before.not_found == 1
    assert after.fetch_failed - before.fetch_failed == 1
    assert after.batch_id == batch
