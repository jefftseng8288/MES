"""Phase 1 harvest scheduling layer tests.

Health-report math is tested purely (no DB/network). The date-based report is
tested against a small known batch inserted through the real chain.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.ingest import ingest_inferred_domain_failure, ingest_inferred_domain_success, ingest_seed
from mes.pipeline import HealthReport, compute_health_for_date


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
    r = HealthReport.from_statuses(datetime.now(UTC).date(), requested=30, statuses=statuses)
    assert r.actual == 10
    assert (r.observed, r.not_found, r.fetch_failed) == (6, 3, 1)
    assert r._pct(r.observed) == "60%"
    assert r._pct(r.not_found) == "30%"
    assert r._pct(r.fetch_failed) == "10%"


def test_report_never_merges_into_single_success_rate() -> None:
    statuses = ["observed", "not_found", "fetch_failed"]
    text = HealthReport.from_statuses(datetime.now(UTC).date(), 3, statuses).format()
    # Each status has its own proportion line (three separate percentages).
    for label in ("observed", "not_found", "fetch_failed"):
        assert f"{label}" in text
    assert text.count("33%") == 3  # each of the 3 statuses = 1/3, shown separately
    assert "主儀表" in text  # fetch_failed flagged as the adjustment dial
    # The only mention of "成功率" is the header stating it is NOT merged into one.
    assert "不合併成單一「成功率」" in text


def test_shortfall_flagged_when_actual_below_requested() -> None:
    r = HealthReport.from_statuses(datetime.now(UTC).date(), requested=30, statuses=["observed"])
    assert r.actual < r.requested
    assert "供給不足" in r.format()


def test_empty_batch_pct_is_dash() -> None:
    r = HealthReport.from_statuses(datetime.now(UTC).date(), requested=30, statuses=[])
    assert r._pct(0) == "—"


async def test_compute_health_for_date_counts_todays_inferred_domain(
    session: AsyncSession,
) -> None:
    # Insert one of each status through the real chain, all dated "now".
    today = datetime.now(UTC).date()
    before = await compute_health_for_date(session, today)

    s1 = await ingest_seed(session, f"Harvest Obs {uuid.uuid4().hex[:6]}")
    await ingest_inferred_domain_success(
        session, s1, raw_url="https://ex-obs.com/", domain="ex-obs.com"
    )
    s2 = await ingest_seed(session, f"Harvest NF {uuid.uuid4().hex[:6]}")
    await ingest_inferred_domain_failure(session, s2, status="not_found")
    s3 = await ingest_seed(session, f"Harvest FF {uuid.uuid4().hex[:6]}")
    await ingest_inferred_domain_failure(session, s3, status="fetch_failed")
    await session.commit()

    after = await compute_health_for_date(session, today)
    assert after.observed - before.observed == 1
    assert after.not_found - before.not_found == 1
    assert after.fetch_failed - before.fetch_failed == 1
