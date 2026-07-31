"""MES 警鈴測試。

evaluate / 診斷邏輯純測(記憶體 BatchStats);load + 記錄對真實 DB。
"""

from __future__ import annotations

import random
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.alarm import (
    ALERT_FETCH_FAILED_HIGH,
    ALERT_JOB_FAILED,
    ALERT_JOB_MISSING,
    ALERT_JOB_NO_OUTPUT,
    ALERT_SUPPLY_LOW,
    ALERT_ZERO_OBSERVED,
    BatchStats,
    JobBeat,
    build_heartbeat,
    build_message,
    evaluate,
    evaluate_jobs,
    load_today_batches,
    run_alarm_check,
)
from mes.config import get_settings
from mes.db.models import AlertLog, JobRunLog
from mes.ingest import ingest_inferred_domain_failure, ingest_inferred_domain_success, ingest_seed
from mes.jobs import (
    JOB_BASELINE,
    JOB_HARVEST,
    JOB_INSIGHT,
    JOB_PROJECTION,
    heartbeat,
)


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
    # 第 5 步起,四條鏈路的心跳也納入巡檢 -> 得先寫入健康心跳,否則會因「從無心跳」而觸發。
    for job, summary in (
        (JOB_BASELINE, {"observed": 30}),
        (JOB_HARVEST, {"selected": 15, "eligible": 500}),
        (JOB_PROJECTION, {"rows_written": 100}),
        (JOB_INSIGHT, {"entities": 5, "produced": 5}),
    ):
        async with heartbeat(job, session_maker=maker) as beat:
            beat.summary = summary
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


# --- 第 5 步:各鏈路心跳 + 警鈴擴充 ------------------------------------------


def _beat(job: str, *, hours_ago: float | None = 1.0, status: str = "success",
          summary: dict[str, Any] | None = None, runs_today: int = 1) -> JobBeat:
    last = None if hours_ago is None else datetime.now(UTC) - timedelta(hours=hours_ago)
    return JobBeat(job=job, last_run=last, last_status=status if last else None,
                   last_summary=summary or {}, runs_today=runs_today)


def _healthy_beats() -> dict[str, JobBeat]:
    return {
        JOB_BASELINE: _beat(JOB_BASELINE, summary={"observed": 20}, runs_today=3),
        JOB_HARVEST: _beat(JOB_HARVEST, summary={"selected": 15, "eligible": 500}, runs_today=8),
        JOB_PROJECTION: _beat(JOB_PROJECTION, summary={"rows_written": 1500}),
        JOB_INSIGHT: _beat(JOB_INSIGHT, summary={"entities": 14, "produced": 12}),
    }


def test_all_jobs_healthy_no_alert() -> None:
    assert evaluate_jobs(_healthy_beats()) == []


def test_job_never_ran_fires() -> None:
    """★ 這次的實況:projection / insight 從未被 load、從無心跳。"""
    beats = _healthy_beats()
    beats[JOB_PROJECTION] = _beat(JOB_PROJECTION, hours_ago=None)
    alerts = evaluate_jobs(beats)
    assert [a.alert_type for a in alerts] == [ALERT_JOB_MISSING]
    assert "從無任何心跳" in alerts[0].detection and "沒 load" in alerts[0].diagnosis


def test_job_silent_beyond_grace_fires() -> None:
    beats = _healthy_beats()
    beats[JOB_HARVEST] = _beat(JOB_HARVEST, hours_ago=20)  # harvest 寬限 9 小時
    alerts = [a for a in evaluate_jobs(beats) if a.alert_type == ALERT_JOB_MISSING]
    assert len(alerts) == 1 and "20.0 小時沒有心跳" in alerts[0].detection


def test_job_within_grace_does_not_fire() -> None:
    """★ 防誤報:insight 23:40 跑、警鈴 23:50 巡檢,只差 10 分鐘不能誤判成沒跑。"""
    beats = _healthy_beats()
    beats[JOB_INSIGHT] = _beat(JOB_INSIGHT, hours_ago=0.17, summary={"entities": 5})
    assert evaluate_jobs(beats) == []


def test_job_failed_fires() -> None:
    beats = _healthy_beats()
    beats[JOB_BASELINE] = _beat(JOB_BASELINE, status="failed", summary={"observed": 0})
    alerts = [a for a in evaluate_jobs(beats) if a.alert_type == ALERT_JOB_FAILED]
    assert len(alerts) == 1 and "執行失敗" in alerts[0].detection


def test_harvest_normal_idle_does_not_fire() -> None:
    """★ 正常閒置不誤報:候選池全被最小重抓間隔 gate 住 → 挑 0 家是自適應的正常結果。"""
    beats = _healthy_beats()
    beats[JOB_HARVEST] = _beat(
        JOB_HARVEST, summary={"selected": 0, "eligible": 0, "gated_by_interval": 592}, runs_today=8)
    assert evaluate_jobs(beats) == []


def test_harvest_zero_selected_with_pool_fires() -> None:
    """★ 異常正確報:有 500 家可抓卻挑到 0 家 → 挑選邏輯壞了。"""
    beats = _healthy_beats()
    beats[JOB_HARVEST] = _beat(
        JOB_HARVEST, runs_today=8,
        summary={"selected": 0, "eligible": 500, "gated_by_interval": 92})
    alerts = [a for a in evaluate_jobs(beats) if a.alert_type == ALERT_JOB_NO_OUTPUT]
    assert len(alerts) == 1
    assert "500 家可抓,卻挑到 0 家" in alerts[0].detection
    assert "非最小間隔造成的正常閒置" in alerts[0].diagnosis


def test_projection_zero_rows_fires() -> None:
    beats = _healthy_beats()
    beats[JOB_PROJECTION] = _beat(JOB_PROJECTION, summary={"rows_written": 0})
    assert [a.alert_type for a in evaluate_jobs(beats)] == [ALERT_JOB_NO_OUTPUT]


def test_baseline_three_alarms_unchanged() -> None:
    """既有 baseline 三警鈴行為不變(心跳擴充不得弄壞它)。"""
    batches = [_b(1, seeds=30, observed=0, fetch_failed=30), _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=30)]
    zeros = [a for a in evaluate(batches) if a.alert_type == ALERT_ZERO_OBSERVED]
    assert len(zeros) == 1 and "限流" in zeros[0].diagnosis


def test_daily_heartbeat_includes_four_chains() -> None:
    batches = [_b(1, seeds=30, observed=29), _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=30)]
    msg = build_heartbeat("2099-01-01", batches, _healthy_beats())
    assert "四條鏈路" in msg
    for label in ("baseline", "harvest", "投影", "Insight"):
        assert label in msg
    assert "seeds 30 / observed 29" in msg  # 既有內容保留


def test_daily_heartbeat_shows_harvest_idle_as_normal() -> None:
    """★ harvest 正常閒置在每日安好要顯示為「閒置」,不是失效。"""
    beats = _healthy_beats()
    beats[JOB_HARVEST] = _beat(
        JOB_HARVEST, summary={"selected": 0, "eligible": 0, "gated_by_interval": 592}, runs_today=8)
    msg = build_heartbeat("2099-01-01", [], beats)
    assert "閒置" in msg and "正常" in msg


# --- 心跳實際寫入 DB(真連 DB)-----------------------------------------------


async def test_heartbeat_writes_row_even_with_zero_output(session: AsyncSession) -> None:
    """★ 產出為 0 也要寫心跳 —— 沒產出 ≠ 沒跑,這是整個機制的重點。"""
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    job = f"testjob_{uuid.uuid4().hex[:8]}"
    async with heartbeat(job, session_maker=maker) as beat:
        beat.summary = {"selected": 0, "eligible": 0}
    row = await session.scalar(select(JobRunLog).where(JobRunLog.job == job))
    assert row is not None and row.status == "success"
    assert row.summary is not None
    assert row.summary["selected"] == 0 and row.summary["eligible"] == 0
    # 心跳自動帶上「這次是哪版 code 跑的」(讓常駐 daemon 跑舊 code 變成可觀測)
    assert "code_version" in row.summary


async def test_heartbeat_records_failure_and_reraises(session: AsyncSession) -> None:
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    job = f"testjob_{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        async with heartbeat(job, session_maker=maker):
            raise RuntimeError("boom")
    row = await session.scalar(select(JobRunLog).where(JobRunLog.job == job))
    assert row is not None and row.status == "failed"
    assert row.error is not None and "boom" in row.error


# --- baseline 診斷改讀心跳(2026-08-01 修正誤診)-----------------------------


def test_zero_observed_with_heartbeat_not_misdiagnosed_as_daemon_dead() -> None:
    """★ 有心跳但撈到 0 → 不可再誤診為「daemon 沒跑」。

    真實誤診案例(2026-07-31 02:00):baseline 有跑、健康報告白紙黑字寫「批次筆數 0」,
    警鈴卻報「批次無記錄 → daemon 沒跑」—— 把人往錯的方向查。錯的診斷比不報更糟。
    """
    b = _b(1, seeds=0, observed=0, exists=False)  # observation_log 上查無此批
    beats = {b.batch_id: {"batch_id": b.batch_id, "actual": 0, "requested": 30}}
    alerts = evaluate([b, _b(2, seeds=30, observed=30), _b(3, seeds=30, observed=30)], beats)
    d = next(a.diagnosis for a in alerts if a.alert_type == ALERT_ZERO_OBSERVED)
    assert "daemon 有跑" in d and "供給問題" in d
    assert "非執行異常" in d  # 明確排除執行異常
    assert "daemon 沒跑" not in d  # 關鍵:不再出現那句誤導人的診斷


def test_zero_observed_without_heartbeat_still_reports_execution_anomaly() -> None:
    """無心跳 → 仍要正確報執行異常(不能因為修了誤診就變成什麼都不報)。"""
    b = _b(1, seeds=0, observed=0, exists=False)
    alerts = evaluate([b, _b(2, seeds=30, observed=30), _b(3, seeds=30, observed=30)], {})
    d = next(a.diagnosis for a in alerts if a.alert_type == ALERT_ZERO_OBSERVED)
    assert "執行異常" in d and "無心跳" in d


def test_zero_observed_with_heartbeat_hitting_cap_says_so() -> None:
    """撈到 0 且觸頂 → 診斷要指出「觸及我們自己的上限,不代表市場沒有了」。"""
    b = _b(1, seeds=0, observed=0, exists=False)
    beats = {b.batch_id: {"batch_id": b.batch_id, "actual": 0,
                          "hit_cap": True, "stop_reason": "time_limit", "last_page": 310}}
    alerts = evaluate([b, _b(2, seeds=30, observed=30), _b(3, seeds=30, observed=30)], beats)
    d = next(a.diagnosis for a in alerts if a.alert_type == ALERT_ZERO_OBSERVED)
    assert "觸及我們自己設的上限" in d and "不代表市場沒有了" in d


def test_zero_observed_with_seeds_still_uses_inference_branches() -> None:
    """有跑也撈到 Seed,卻 0 observed → 仍走既有的推論失敗分支(不被心跳短路)。"""
    b = _b(1, seeds=30, observed=0, fetch_failed=30)
    beats = {b.batch_id: {"batch_id": b.batch_id, "actual": 30}}
    alerts = evaluate([b, _b(2, seeds=30, observed=30), _b(3, seeds=30, observed=30)], beats)
    d = next(a.diagnosis for a in alerts if a.alert_type == ALERT_ZERO_OBSERVED)
    assert "限流" in d  # fetch_failed 佔滿的既有診斷仍生效


def test_evaluate_without_beats_keeps_old_behaviour() -> None:
    """不傳 batch_beats 時退回舊行為(既有呼叫端與測試不受影響)。"""
    batches = [_b(1, seeds=0, observed=0, exists=False), _b(2, seeds=30, observed=30),
               _b(3, seeds=30, observed=30)]
    assert any(a.alert_type == ALERT_ZERO_OBSERVED for a in evaluate(batches))


def test_daily_heartbeat_flags_stale_code() -> None:
    """★ 常駐 daemon 跑舊 code → 每日安好要看得出來(2026-08-01 踩到:harvest 跑了 16 天舊 code)。"""
    from mes.jobs import CODE_VERSION

    beats = _healthy_beats()
    beats[JOB_HARVEST] = _beat(
        JOB_HARVEST, runs_today=8,
        summary={"selected": 15, "eligible": 500, "code_version": "0000000"})
    msg = build_heartbeat("2099-01-01", [], beats)
    assert "跑的是舊 code" in msg and "0000000" in msg
    assert "需重啟" in msg

    # 版本一致時不誤報
    beats[JOB_HARVEST] = _beat(
        JOB_HARVEST, runs_today=8,
        summary={"selected": 15, "eligible": 500, "code_version": CODE_VERSION})
    assert "跑的是舊 code" not in build_heartbeat("2099-01-01", [], beats)
