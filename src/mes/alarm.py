"""MES 警鈴(主動回報)—— 系統的痛覺神經。

每天 23:50(台灣)獨立跑一次,讀「當天(台灣日期)已完成的三批 baseline」(-01/-02/-03)
巡檢三個警鈴條件;觸發時**帶原因診斷**推 Telegram,並結構化記錄進 `alert_log`(未來自動
調整策略要學的燃料)。

定位鐵律:**只做主動回報 + 初步診斷,不做任何自動調整 / 退避 / 加來源。** 判斷怎麼調、要
不要調,由 Jeff 決定。不跨日(跨日交界的連續異常會漏報——已知且接受)。門檻為暫定起點。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import AlertLog, JobRunLog, ObservationLog
from mes.ingest import FEATURE_OBSERVED_ON_APP_STORE
from mes.jobs import (
    ALL_JOBS,
    CODE_VERSION,
    EXPECTED_RUNS_PER_DAY,
    JOB_BASELINE,
    JOB_HARVEST,
    JOB_INSIGHT,
    JOB_PROJECTION,
)
from mes.notify import send_telegram

logger = logging.getLogger(__name__)
_TAIPEI = ZoneInfo("Asia/Taipei")

# 門檻:暫定起點,待實況回饋調整(非已驗證安全基準)。
SUPPLY_LOW_THRESHOLD = 10  # seeds < 10
FETCH_FAILED_HIGH_THRESHOLD = 15  # fetch_failed > 15

# baseline 三個固定排程時段(對應 batch_id -0N)。
SLOT_LABELS = {1: "02:00", 2: "10:00", 3: "21:00"}

ALERT_ZERO_OBSERVED = "zero_observed"
ALERT_FETCH_FAILED_HIGH = "fetch_failed_high"
ALERT_SUPPLY_LOW = "supply_low"
# 鏈路心跳類(第 5 步新增)。
ALERT_JOB_MISSING = "job_missing"  # 該跑而沒跑
ALERT_JOB_FAILED = "job_failed"  # 跑了但報錯
ALERT_JOB_NO_OUTPUT = "job_no_output"  # 跑了但產出異常為 0(已排除正常閒置)

# 各 job「多久沒心跳就算失聯」的寬限窗(小時)。**暫定值,待實況調整。**
# ⚠️ 刻意寬鬆:警鈴 23:50 跑,距 insight(23:40)僅 10 分鐘,若 insight 跑久一點會被
# 誤判「沒跑」。故日更型 job 用 25 小時窗(「最近 25 小時內有沒有跑過」)而非「今天有沒有跑」。
# harvest 每 3 小時一批,給 9 小時(約 3 個週期)仍能當天抓到死掉。
GRACE_HOURS = {
    JOB_BASELINE: 25.0, JOB_HARVEST: 9.0, JOB_PROJECTION: 25.0, JOB_INSIGHT: 25.0,
}
_JOB_LABELS = {
    JOB_BASELINE: "baseline", JOB_HARVEST: "harvest",
    JOB_PROJECTION: "投影", JOB_INSIGHT: "Insight",
}


@dataclass(frozen=True)
class BatchStats:
    slot: int
    batch_id: str
    exists: bool  # 這批有沒有任何記錄(沒有 = 疑似沒跑)
    seeds: int  # 撈到的新 Seed 數(observed_on_app_store)
    observed: int
    not_found: int
    fetch_failed: int

    @property
    def label(self) -> str:
        return f"{SLOT_LABELS[self.slot]}({self.batch_id})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "batch_id": self.batch_id, "exists": self.exists,
            "seeds": self.seeds, "observed": self.observed,
            "not_found": self.not_found, "fetch_failed": self.fetch_failed,
        }


@dataclass
class Alert:
    alert_type: str
    detection: str  # 偵測到什麼(數字)
    diagnosis: str  # 最可能原因
    detail: dict[str, Any] = field(default_factory=dict)


async def load_today_batches(session: AsyncSession, taiwan_date: str) -> list[BatchStats]:
    """讀當天三批(-01/-02/-03)的三值 + 供給。缺的批 = exists False、全 0。"""
    batch_ids = [f"{taiwan_date}-0{slot}" for slot in (1, 2, 3)]
    rows = await session.execute(
        select(
            ObservationLog.batch_id, ObservationLog.feature, ObservationLog.status, func.count()
        )
        .where(ObservationLog.batch_id.in_(batch_ids))
        .group_by(ObservationLog.batch_id, ObservationLog.feature, ObservationLog.status)
    )
    agg: dict[str, dict[str, int]] = {b: {} for b in batch_ids}
    for batch_id, feature, status, n in rows.all():
        key = "seed" if feature == FEATURE_OBSERVED_ON_APP_STORE else status
        agg[batch_id][key] = agg[batch_id].get(key, 0) + n

    stats: list[BatchStats] = []
    for slot in (1, 2, 3):
        b = f"{taiwan_date}-0{slot}"
        a = agg[b]
        stats.append(
            BatchStats(
                slot=slot, batch_id=b, exists=bool(a),
                seeds=a.get("seed", 0), observed=a.get("observed", 0),
                not_found=a.get("not_found", 0), fetch_failed=a.get("fetch_failed", 0),
            )
        )
    return stats


async def load_baseline_batch_beats(
    session: AsyncSession, taiwan_date: str
) -> dict[str, dict[str, Any]]:
    """讀當天 baseline 各批的心跳 summary,以 batch_id 為鍵。

    心跳是 per-run 的,而診斷要問的是「**這一批**跑了沒」,故以 summary 裡的 batch_id 對應。
    """
    rows = (
        await session.execute(
            select(JobRunLog).where(
                JobRunLog.job == JOB_BASELINE,
                func.to_char(func.timezone("Asia/Taipei", JobRunLog.finished_at), "YYYY-MM-DD")
                == taiwan_date,
            )
        )
    ).scalars().all()
    beats: dict[str, dict[str, Any]] = {}
    for r in rows:
        batch_id = (r.summary or {}).get("batch_id")
        if batch_id:
            beats[str(batch_id)] = r.summary or {}
    return beats


def _diagnose_zero_observed(b: BatchStats, beat_summary: dict[str, Any] | None = None) -> str:
    """0 observed 的原因分辨(對應完全不同的調整方向)。

    ★ **先讀心跳,再看 observation_log。** 因為「沒跑」與「跑了但一個新 Seed 都沒撈到」
    在 observation_log 上**長得一模一樣**(都是查無此批號)—— 這正是心跳存在的理由。
    先前沒讀心跳時,此函式會把「有跑但撈到 0」誤診為「daemon 沒跑」,**把人往錯的方向查**
    (實測 2026-07-31 02:00 那批就被誤診過)。**錯的診斷比不報更糟。**

    `beat_summary` = 該批號對應的 baseline 心跳 summary;None 表示查無心跳。
    """
    if beat_summary is not None:
        # 有心跳 = daemon 確實跑了 -> 排除「執行異常」,往供給/限流方向診斷。
        gathered = int(beat_summary.get("actual", 0))
        if gathered == 0:
            extra = ""
            if beat_summary.get("hit_cap"):
                extra = (
                    f"(且**觸及我們自己設的上限**:{beat_summary.get('stop_reason')},"
                    f"翻到第 {beat_summary.get('last_page')} 頁 —— 不代表市場沒有了)"
                )
            return (
                "✅ daemon 有跑(心跳存在),但一個新 Seed 都沒撈到 → "
                f"**Seed 來源供給問題,非執行異常**{extra}"
            )
        # 有跑也撈到 Seed,卻 0 observed -> 落到下面的推論失敗分支。
    elif not b.exists:
        return (
            "批次無記錄**且無心跳** → 批次執行異常(daemon 沒跑 / 報錯 / 沒 load,疑似系統問題)"
        )
    if b.seeds == 0:
        return "無新 Seed(供給 0)→ 池子乾(該加來源)"
    if b.fetch_failed > 0 and b.fetch_failed >= b.not_found:
        return f"fetch_failed 佔滿({b.fetch_failed} 筆)→ 疑似限流 / DDG 問題"
    if b.not_found > 0:
        return f"not_found 佔滿({b.not_found} 筆)→ 這批 Seed 全數搜不到 domain(市場事實,未必需動作)"
    return "0 observed 但成因不明確,需人工查看"


def evaluate(
    batches: list[BatchStats], batch_beats: dict[str, dict[str, Any]] | None = None
) -> list[Alert]:
    """三警鈴巡檢。回傳觸發的 Alert(可多個,合併推送)。

    `batch_beats` = batch_id -> 該批 baseline 心跳的 summary(見 load_baseline_batch_beats)。
    給了才能分辨「沒跑」vs「跑了但撈到 0」;不給則退回舊行為(僅看 observation_log)。
    """
    batch_beats = batch_beats or {}
    day_snapshot = [b.as_dict() for b in batches]
    thresholds = {
        "supply_low": SUPPLY_LOW_THRESHOLD, "fetch_failed_high": FETCH_FAILED_HIGH_THRESHOLD,
    }
    alerts: list[Alert] = []

    # 3. 0 筆 observed —— 單批即觸發,必帶原因診斷。
    for b in batches:
        if b.observed == 0:
            alerts.append(Alert(
                ALERT_ZERO_OBSERVED,
                detection=f"{b.label} observed = 0",
                diagnosis=_diagnose_zero_observed(b, batch_beats.get(b.batch_id)),
                detail={"trigger_slot": b.slot, "batches": day_snapshot, "thresholds": thresholds},
            ))

    # 2. fetch_failed 過高 —— 連續兩批 > 15。
    for a, c in zip(batches, batches[1:], strict=False):
        if a.exists and c.exists and a.fetch_failed > FETCH_FAILED_HIGH_THRESHOLD \
                and c.fetch_failed > FETCH_FAILED_HIGH_THRESHOLD:
            alerts.append(Alert(
                ALERT_FETCH_FAILED_HIGH,
                detection=f"{a.label} 與 {c.label} 連續兩批 fetch_failed 過高"
                          f"({a.fetch_failed} / {c.fetch_failed} 筆)",
                diagnosis="連續兩批被大量 fetch_failed → 疑似 DDG 限流(一天總量或節奏太密)",
                detail={"trigger_slots": [a.slot, c.slot], "batches": day_snapshot,
                        "thresholds": thresholds},
            ))

    # 1. 池子供給不足 —— 連續兩批 seeds < 10。
    for a, c in zip(batches, batches[1:], strict=False):
        if a.exists and c.exists and a.seeds < SUPPLY_LOW_THRESHOLD \
                and c.seeds < SUPPLY_LOW_THRESHOLD:
            alerts.append(Alert(
                ALERT_SUPPLY_LOW,
                detection=f"{a.label} 與 {c.label} 連續兩批新 Seed 過少"
                          f"({a.seeds} / {c.seeds} 筆)",
                diagnosis="連續兩批撈不到足量新 Seed → 池子供給不足(該加來源 / 翻更深)",
                detail={"trigger_slots": [a.slot, c.slot], "batches": day_snapshot,
                        "thresholds": thresholds},
            ))
    return alerts


@dataclass(frozen=True)
class JobBeat:
    """某 job 的心跳現況(最近一次 + 當天次數),供警鈴與每日安好共用。"""

    job: str
    last_run: datetime | None  # 最近一次執行結束時間(None = 從無心跳)
    last_status: str | None  # success / failed
    last_summary: dict[str, Any]
    runs_today: int  # 當天(台灣日)跑了幾次

    @property
    def label(self) -> str:
        return _JOB_LABELS.get(self.job, self.job)


async def load_job_beats(
    session: AsyncSession, taiwan_date: str, *, now: datetime | None = None
) -> dict[str, JobBeat]:
    """讀四條鏈路的心跳現況。從無心跳 → last_run=None(這正是「根本沒跑」的訊號)。"""
    now = now or datetime.now(UTC)
    beats: dict[str, JobBeat] = {}
    for job in ALL_JOBS:
        row = (
            await session.execute(
                select(JobRunLog).where(JobRunLog.job == job)
                .order_by(JobRunLog.finished_at.desc()).limit(1)
            )
        ).scalars().first()
        runs_today = int(await session.scalar(
            select(func.count()).select_from(JobRunLog).where(
                JobRunLog.job == job,
                func.to_char(func.timezone("Asia/Taipei", JobRunLog.finished_at), "YYYY-MM-DD")
                == taiwan_date,
            )
        ) or 0)
        beats[job] = JobBeat(
            job=job,
            last_run=row.finished_at if row else None,
            last_status=row.status if row else None,
            last_summary=(row.summary or {}) if row else {},
            runs_today=runs_today,
        )
    return beats


def _harvest_idle_is_normal(summary: dict[str, Any]) -> bool:
    """★ harvest「挑到 0 家」是**正常閒置**還是**異常**。

    正常閒置:候選池全被最小重抓間隔 gate 住(eligible == 0)—— 這是自適應的正常結果。
    異常:eligible > 0(有店可抓)卻挑到 0 家 → 挑選邏輯壞了,該叫。
    """
    return int(summary.get("eligible", 0)) == 0


def evaluate_jobs(beats: dict[str, JobBeat], *, now: datetime | None = None) -> list[Alert]:
    """四條鏈路的心跳巡檢:沒跑 / 報錯 / 跑了但產出異常為 0。"""
    now = now or datetime.now(UTC)
    alerts: list[Alert] = []
    for job, beat in beats.items():
        grace = GRACE_HOURS.get(job, 25.0)
        detail = {"job": job, "runs_today": beat.runs_today,
                  "last_run": beat.last_run.isoformat() if beat.last_run else None,
                  "grace_hours": grace, "summary": beat.last_summary}

        # 1. 該跑而沒跑(含「從來沒跑過」——這次 projection/insight 就是這種)
        if beat.last_run is None:
            alerts.append(Alert(
                ALERT_JOB_MISSING, detection=f"{beat.label} 從無任何心跳記錄",
                diagnosis=f"{beat.label} 可能從未被排程觸發(daemon 沒 load / plist 沒掛)",
                detail=detail))
            continue
        idle_h = (now - beat.last_run).total_seconds() / 3600
        if idle_h > grace:
            alerts.append(Alert(
                ALERT_JOB_MISSING,
                detection=f"{beat.label} 已 {idle_h:.1f} 小時沒有心跳(寬限 {grace:g} 小時)",
                diagnosis=f"{beat.label} 疑似停擺(daemon 掛掉 / 排程沒觸發 / 程序啟動即失敗)",
                detail=detail))
            continue

        # 2. 跑了但報錯
        if beat.last_status == "failed":
            alerts.append(Alert(
                ALERT_JOB_FAILED, detection=f"{beat.label} 最近一次執行失敗",
                diagnosis=f"{beat.label} 有跑但拋出例外,詳見 job_run_log.error",
                detail=detail))
            continue

        # 3. 跑了但產出異常為 0(★ 必須排除正常閒置)
        s = beat.last_summary
        if job == JOB_HARVEST and int(s.get("selected", 0)) == 0:
            if not _harvest_idle_is_normal(s):
                alerts.append(Alert(
                    ALERT_JOB_NO_OUTPUT,
                    detection=f"harvest 候選池有 {s.get('eligible', 0)} 家可抓,卻挑到 0 家",
                    diagnosis="挑選邏輯異常(可抓的店存在但沒被挑中),非最小間隔造成的正常閒置",
                    detail=detail))
        elif job == JOB_PROJECTION and int(s.get("rows_written", -1)) == 0:
            alerts.append(Alert(
                ALERT_JOB_NO_OUTPUT, detection="投影跑完但寫出 0 列 knowledge_state",
                diagnosis="observation_log 可能無資料,或投影取值全被排除 —— 需人工查看",
                detail=detail))
        elif job == JOB_INSIGHT and int(s.get("entities", -1)) == 0:
            alerts.append(Alert(
                ALERT_JOB_NO_OUTPUT, detection="Insight 跑完但沒有任何 entity 可描述",
                diagnosis="knowledge_state 沒有 store 的市場特徵(harvest 可能沒產出)",
                detail=detail))
    return alerts


def build_message(taiwan_date: str, alerts: list[Alert]) -> str:
    lines = [f"⚠️ [MES 警鈴] {taiwan_date}", ""]
    for i, a in enumerate(alerts, 1):
        lines.append(f"{i}. 偵測:{a.detection}")
        lines.append(f"   最可能原因:{a.diagnosis}")
    lines.append("")
    lines.append("(參考,非自動調整;是否調整由你判斷)")
    return "\n".join(lines)


def _job_line(beat: JobBeat) -> str:
    """一條鏈路在每日安好裡的一行狀態。"""
    expected = EXPECTED_RUNS_PER_DAY.get(beat.job, 1)
    if beat.last_run is None:
        return f"{beat.label}:❌ 從無心跳"
    mark = "✓" if beat.runs_today >= expected else "⚠️"
    if beat.last_status == "failed":
        mark = "❌"
    detail = f"{beat.runs_today}/{expected}"
    # ★ 跑的是不是最新 code —— 常駐 daemon 會把程式碼凍結在啟動當下,改了 code 不重啟
    # 就一直跑舊邏輯,而且 log 照常、批次照常,完全沒有訊號(2026-08-01 實際踩到:
    # harvest 跑了 16 天舊 code)。心跳帶 code_version 後,這件事變成看得見的。
    ran_version = beat.last_summary.get("code_version")
    if ran_version and CODE_VERSION and ran_version != CODE_VERSION:
        return (f"{beat.label}:{detail} ⚠️ 跑的是舊 code({ran_version},"
                f"目前 {CODE_VERSION})—— 常駐 daemon 需重啟")
    # harvest 挑到 0 家要能看出是「正常閒置」而非失效。
    if beat.job == JOB_HARVEST and int(beat.last_summary.get("selected", -1)) == 0:
        if _harvest_idle_is_normal(beat.last_summary):
            gated = beat.last_summary.get("gated_by_interval", 0)
            return f"{beat.label}:{detail} 😴 閒置(候選池 {gated} 家都在最小重抓間隔內,正常)"
        return f"{beat.label}:{detail} ⚠️ 挑到 0 家(候選池仍有可抓的)"
    return f"{beat.label}:{detail} {mark}"


def build_heartbeat(
    taiwan_date: str, batches: list[BatchStats], beats: dict[str, JobBeat] | None = None
) -> str:
    """無警鈴時的每日安好摘要 —— 心跳,證明四條鏈路都活著。

    **為什麼要含四條鏈路:** 警鈴只在異常時推,正常時 Jeff 看不到其他鏈路狀態 ——
    這次 projection / insight 靜默失效半個月,正是這個盲區造成的。
    """
    lines = [f"✅ [MES 每日安好] {taiwan_date}", ""]
    for b in batches:
        if b.exists:
            lines.append(f"{SLOT_LABELS[b.slot]}:seeds {b.seeds} / observed {b.observed}")
        else:
            lines.append(f"{SLOT_LABELS[b.slot]}:(無記錄)")
    if beats:
        lines.append("")
        lines.append("四條鏈路(當天執行次數/預期):")
        for job in ALL_JOBS:
            if job in beats:
                lines.append(f"  {_job_line(beats[job])}")
    lines.append("")
    lines.append("無警鈴。")
    return "\n".join(lines)


async def _record(session: AsyncSession, taiwan_date: str, alert: Alert, delivered: bool) -> None:
    session.add(AlertLog(
        taiwan_date=taiwan_date, alert_type=alert.alert_type,
        diagnosis=f"{alert.detection} → {alert.diagnosis}",
        detail=alert.detail, delivered=delivered, fired_at=datetime.now(UTC),
    ))


async def run_alarm_check(
    *,
    taiwan_date: str | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    send: bool = True,
) -> list[Alert]:
    """巡檢當天三批。有異常 → 推警鈴 + 記 alert_log;無異常 → 推每日安好摘要(心跳,不記 DB)。

    回傳觸發的 Alert(無異常回空 list)。
    """
    if taiwan_date is None:
        taiwan_date = datetime.now(_TAIPEI).date().isoformat()
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            batches = await load_today_batches(session, taiwan_date)
            beats = await load_job_beats(session, taiwan_date)
            batch_beats = await load_baseline_batch_beats(session, taiwan_date)
            # baseline 三警鈴(既有,不變;診斷改讀心跳)+ 四條鏈路心跳巡檢。
            alerts = evaluate(batches, batch_beats) + evaluate_jobs(beats)
            if not alerts:
                # 無異常也主動回報一則「安好摘要」當心跳(證明 daemon 活著)。
                # 心跳不是異常,不寫 alert_log(alert_log 保持只記異常)。
                heartbeat = build_heartbeat(taiwan_date, batches, beats)
                delivered = send_telegram(heartbeat) if send else False
                logger.info("[alarm] %s 無異常,推每日安好摘要 delivered=%s", taiwan_date, delivered)
                print(heartbeat)
                return []
            message = build_message(taiwan_date, alerts)
            delivered = send_telegram(message) if send else False
            for alert in alerts:
                await _record(session, taiwan_date, alert, delivered)
            await session.commit()
            logger.warning("[alarm] %s 觸發 %d 個警鈴,Telegram delivered=%s",
                           taiwan_date, len(alerts), delivered)
            print(message)
            return alerts
    finally:
        if engine is not None:
            await engine.dispose()


def main() -> None:
    asyncio.run(run_alarm_check())


if __name__ == "__main__":
    main()
