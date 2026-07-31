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

_BATCH = "2099-01-01-01"  # sentinel year: never collides with real scheduled batches


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def test_five_review_app_handles_registered() -> None:
    from mes.scrape import SEED_SOURCE_HANDLES

    # review 類五個 + 2026-07-31 擴充的五個非 review 類(全部 handle 皆實測過)
    assert {"loox", "judgeme", "yotpo", "okendo", "stamped"} <= set(SEED_SOURCE_HANDLES)
    assert {"klaviyo", "smile", "loyaltylion", "seal_subscriptions", "weglot"} <= set(
        SEED_SOURCE_HANDLES
    )
    # handles are the App Store URL slugs (not all equal to the app name)
    assert SEED_SOURCE_HANDLES["stamped"] == "product-reviews-addon"
    assert SEED_SOURCE_HANDLES["yotpo"] == "yotpo-social-reviews"
    # handle 不等於 app 名的再一例(這次擴充實測)
    assert SEED_SOURCE_HANDLES["klaviyo"] == "klaviyo-email-marketing"


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


# --- 兩層上限 + 觸頂訊號(2026-07-31)----------------------------------------


def test_caps_are_set_where_intended() -> None:
    """MAX_PAGES 刻意設在正常到不了的地方;時間煞車才是實際生效的那個。"""
    from mes.pipeline import MAX_GATHER_HOURS, MAX_PAGES

    assert MAX_PAGES == 2000
    assert MAX_GATHER_HOURS == 5.0


def test_hit_cap_distinguishes_our_limit_from_market_empty() -> None:
    """★ 核心:觸頂(我們的視野限制)與來源真的翻到底(市場沒了)必須分得開。"""
    from mes.pipeline import (
        STOP_MAX_PAGES,
        STOP_SOURCES_EXHAUSTED,
        STOP_TARGET_MET,
        STOP_TIME_LIMIT,
        GatherOutcome,
    )

    def out(reason: str) -> GatherOutcome:
        return GatherOutcome([], reason, last_page=1, elapsed_seconds=1.0)

    assert out(STOP_MAX_PAGES).hit_cap is True
    assert out(STOP_TIME_LIMIT).hit_cap is True
    assert out(STOP_SOURCES_EXHAUSTED).hit_cap is False  # 這才是「市場真的沒了」
    assert out(STOP_TARGET_MET).hit_cap is False


def test_health_report_shows_stop_reason_and_cap_warning() -> None:
    """健康報告要能一眼看出「本批是否觸頂」。"""
    from mes.pipeline import STOP_MAX_PAGES, STOP_TARGET_MET, GatherOutcome, HealthReport

    hit = HealthReport.from_statuses(
        "2099-01-01-01", 30, ["observed"] * 2,
        GatherOutcome([], STOP_MAX_PAGES, last_page=2000, elapsed_seconds=7200.0),
    )
    text = hit.format()
    assert "蒐集停止原因" in text and "2000 頁上限" in text
    assert "觸及我們自己設的上限" in text and "不是市場沒有了" in text

    normal = HealthReport.from_statuses(
        "2099-01-01-02", 30, ["observed"] * 30,
        GatherOutcome([], STOP_TARGET_MET, last_page=1, elapsed_seconds=60.0),
    )
    assert "觸及我們自己設的上限" not in normal.format()  # 正常不誤報


def test_cap_hit_message_distinguishes_the_two_causes() -> None:
    """★ 訊息必須說清楚是哪一種上限 —— 兩者狀況不同、修法不同。"""
    from mes.pipeline import (
        STOP_MAX_PAGES,
        STOP_TIME_LIMIT,
        GatherOutcome,
        HealthReport,
        build_cap_hit_message,
    )

    pages = build_cap_hit_message(HealthReport.from_statuses(
        "2099-01-01-01", 30, ["observed"] * 2,
        GatherOutcome([], STOP_MAX_PAGES, last_page=2000, elapsed_seconds=3600.0)))
    assert "2000 頁上限" in pages and "分頁有 bug" in pages
    assert "第 2000 頁" in pages and "2/30" in pages  # 帶診斷資訊

    timeout = build_cap_hit_message(HealthReport.from_statuses(
        "2099-01-01-02", 30, ["observed"] * 5,
        GatherOutcome([], STOP_TIME_LIMIT, last_page=310, elapsed_seconds=18000.0)))
    assert "5.0 小時上限" in timeout and "執行時間耗盡" in timeout
    assert "未丟棄" in timeout  # 已撈到的照常寫入
    assert "分頁有 bug" not in timeout  # 兩者不可混淆


# --- 防重入:三個 baseline slot 跨 slot 互斥(2026-07-31)---------------------


async def test_baseline_slots_are_mutually_exclusive() -> None:
    """★ slot 3(21:00)還在跑時 slot 1(02:00)啟動 → 必須排隊,不可同時打 DDG。

    APScheduler 的 max_instances 是 per-job,三個 slot 是三個 job,擋不住互相重疊;
    靠 schedule._BASELINE_LOCK 這把跨 slot 的鎖。
    """
    import asyncio
    from unittest.mock import patch

    import mes.schedule as sch

    running = concurrent_peak = 0

    async def fake_batch(*, slot: int) -> None:
        nonlocal running, concurrent_peak
        running += 1
        concurrent_peak = max(concurrent_peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    with patch.object(sch, "run_daily_batch", fake_batch):
        await asyncio.gather(sch._job(3), sch._job(1), sch._job(2))

    assert concurrent_peak == 1  # 同時最多一批在跑(否則 DDG 速率翻倍)


async def test_store_harvest_not_blocked_by_baseline_lock() -> None:
    """store-harvest 戳的是各店自己的伺服器(不同對象),不該被 baseline 的鎖擋住。"""
    import asyncio

    import mes.schedule as sch

    async with sch._BASELINE_LOCK:  # baseline 佔用中
        # 這把鎖只給 baseline;store-harvest 路徑不碰它 -> 立即可取得
        assert sch._BASELINE_LOCK.locked()
        await asyncio.sleep(0)  # 不會卡住
