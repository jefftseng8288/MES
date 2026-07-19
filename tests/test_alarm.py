"""MES 警鈴測試。

evaluate / 診斷邏輯純測(記憶體 BatchStats);load + 記錄對真實 DB。
"""

from __future__ import annotations

import random
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.alarm import (
    ALERT_FETCH_FAILED_HIGH,
    ALERT_SUPPLY_LOW,
    ALERT_ZERO_OBSERVED,
    BatchStats,
    build_heartbeat,
    build_message,
    evaluate,
    load_today_batches,
    run_alarm_check,
)
from mes.config import get_settings
from mes.db.models import AlertLog
from mes.ingest import ingest_inferred_domain_failure, ingest_inferred_domain_success, ingest_seed


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _b(slot: int, *, seeds: int, observed: int, not_found: int = 0, fetch_failed: int = 0,
       exists: bool = True) -> BatchStats:
    return BatchStats(slot=slot, batch_id=f"2099-03-03-0{slot}", exists=exists, seeds=seeds,
                      observed=observed, not_found=not_found, fetch_failed=fetch_failed)


# --- evaluate: three alarm conditions ---------------------------------------


def test_healthy_day_no_alerts() -> None:
    batches = [_b(1, seeds=30, observed=29, fetch_failed=1),
               _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=30)]
    assert evaluate(batches) == []


def test_supply_low_consecutive_fires() -> None:
    batches = [_b(1, seeds=5, observed=5), _b(2, seeds=8, observed=8),
               _b(3, seeds=30, observed=30)]
    types = [a.alert_type for a in evaluate(batches)]
    assert ALERT_SUPPLY_LOW in types


def test_supply_low_non_consecutive_does_not_fire() -> None:
    # low / fine / low — no adjacent pair both below threshold
    batches = [_b(1, seeds=5, observed=5), _b(2, seeds=30, observed=30),
               _b(3, seeds=8, observed=8)]
    assert not any(a.alert_type == ALERT_SUPPLY_LOW for a in evaluate(batches))


def test_fetch_failed_high_consecutive_fires() -> None:
    batches = [_b(1, seeds=30, observed=12, fetch_failed=18),
               _b(2, seeds=30, observed=10, fetch_failed=20),
               _b(3, seeds=30, observed=30)]
    alerts = [a for a in evaluate(batches) if a.alert_type == ALERT_FETCH_FAILED_HIGH]
    assert len(alerts) == 1 and "限流" in alerts[0].diagnosis


def test_zero_observed_single_batch_fires_with_diagnosis() -> None:
    batches = [_b(1, seeds=30, observed=30), _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=0, fetch_failed=30)]
    zeros = [a for a in evaluate(batches) if a.alert_type == ALERT_ZERO_OBSERVED]
    assert len(zeros) == 1 and "限流" in zeros[0].diagnosis


def test_zero_observed_diagnosis_branches() -> None:
    # not_found dominant -> market fact
    d = evaluate([_b(1, seeds=30, observed=0, not_found=30), _b(2, seeds=30, observed=30),
                  _b(3, seeds=30, observed=30)])
    assert "搜不到 domain" in next(a.diagnosis for a in d if a.alert_type == ALERT_ZERO_OBSERVED)
    # seeds 0 -> pool dry
    d = evaluate([_b(1, seeds=0, observed=0), _b(2, seeds=30, observed=30),
                  _b(3, seeds=30, observed=30)])
    assert "池子乾" in next(a.diagnosis for a in d if a.alert_type == ALERT_ZERO_OBSERVED)
    # missing batch -> execution anomaly
    d = evaluate([_b(1, seeds=0, observed=0, exists=False), _b(2, seeds=30, observed=30),
                  _b(3, seeds=30, observed=30)])
    assert "執行異常" in next(a.diagnosis for a in d if a.alert_type == ALERT_ZERO_OBSERVED)


def test_build_message_has_footer_and_no_auto_adjust() -> None:
    batches = [_b(1, seeds=0, observed=0, exists=False), _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=30)]
    msg = build_message("2099-03-03", evaluate(batches))
    assert "MES 警鈴" in msg and "最可能原因" in msg
    assert "非自動調整" in msg  # 定位:只回報不自動調整


# --- notify: credential-gated no-op -----------------------------------------


def test_send_telegram_noop_without_credentials(monkeypatch: Any) -> None:
    # Force empty credentials (deterministic: don't depend on / actually hit the real .env
    # bot — that would spam Telegram on every test run) -> graceful False, no raise.
    import mes.notify as notify

    monkeypatch.setattr(
        notify, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="", telegram_chat_id=""),
    )
    assert notify.send_telegram("test") is False


# --- DB: load + record ------------------------------------------------------


async def _seed_batch(session: AsyncSession, batch_id: str, *, observed: int, not_found: int,
                      fetch_failed: int) -> None:
    for _ in range(observed):
        s = await ingest_seed(session, f"AlarmT {uuid.uuid4().hex[:8]}", batch_id=batch_id)
        await ingest_inferred_domain_success(
            session, s, raw_url="https://a-x.com/", domain=f"a-{uuid.uuid4().hex[:8]}.com",
            batch_id=batch_id)
    for st, count in (("not_found", not_found), ("fetch_failed", fetch_failed)):
        for _ in range(count):
            s = await ingest_seed(session, f"AlarmT {uuid.uuid4().hex[:8]}", batch_id=batch_id)
            await ingest_inferred_domain_failure(session, s, status=st, batch_id=batch_id)
    await session.commit()


def _uniq_date() -> str:
    """Unique date-shaped string per run (batch_id CHECK只驗數字樣式,非真實日曆);
    避免 dev DB 的 Append-Only 累積在重跑時疊加。"""
    return f"{random.randint(3000, 9998)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


async def test_load_today_batches_aggregates(session: AsyncSession) -> None:
    day = _uniq_date()
    await _seed_batch(session, f"{day}-01", observed=3, not_found=1, fetch_failed=0)
    batches = await load_today_batches(session, day)
    b1 = batches[0]
    assert b1.slot == 1 and b1.exists
    assert b1.observed == 3 and b1.not_found == 1
    assert b1.seeds == 4  # 4 seeds gathered (3 obs + 1 nf)
    # missing -02/-03
    assert batches[1].exists is False and batches[1].observed == 0


async def test_run_alarm_check_records_when_fired_and_silent_otherwise(
    session: AsyncSession,
) -> None:
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)

    # A day with a zero-observed batch (all fetch_failed) -> fires, records, delivered False.
    day = _uniq_date()
    await _seed_batch(session, f"{day}-01", observed=0, not_found=0, fetch_failed=2)
    alerts = await run_alarm_check(taiwan_date=day, session_maker=maker, send=False)
    assert any(a.alert_type == ALERT_ZERO_OBSERVED for a in alerts)
    rows = (await session.execute(
        select(AlertLog).where(AlertLog.taiwan_date == day)
    )).scalars().all()
    assert len(rows) == len(alerts) >= 1
    assert all(r.delivered is False for r in rows)
    assert rows[0].detail is not None and "batches" in rows[0].detail

    # A healthy day -> no alerts, no records (heartbeat is sent but not recorded to alert_log).
    day2 = _uniq_date()
    await _seed_batch(session, f"{day2}-01", observed=30, not_found=0, fetch_failed=0)
    await _seed_batch(session, f"{day2}-02", observed=30, not_found=0, fetch_failed=0)
    await _seed_batch(session, f"{day2}-03", observed=30, not_found=0, fetch_failed=0)
    alerts2 = await run_alarm_check(taiwan_date=day2, session_maker=maker, send=False)
    assert alerts2 == []
    rows2 = (await session.execute(
        select(AlertLog).where(AlertLog.taiwan_date == day2)
    )).scalars().all()
    assert rows2 == []


def test_build_heartbeat_summarises_three_batches() -> None:
    batches = [
        BatchStats(slot=1, batch_id="2099-01-01-01", exists=True, seeds=30, observed=28,
                   not_found=0, fetch_failed=0),
        BatchStats(slot=2, batch_id="2099-01-01-02", exists=True, seeds=25, observed=27,
                   not_found=0, fetch_failed=0),
        BatchStats(slot=3, batch_id="2099-01-01-03", exists=True, seeds=22, observed=24,
                   not_found=0, fetch_failed=0),
    ]
    msg = build_heartbeat("2099-01-01", batches)
    assert "每日安好" in msg and "2099-01-01" in msg
    assert "02:00:seeds 30 / observed 28" in msg
    assert "無警鈴" in msg
