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
from mes.db.models import AlertLog, ObservationLog
from mes.ingest import FEATURE_OBSERVED_ON_APP_STORE
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


def _diagnose_zero_observed(b: BatchStats) -> str:
    """0 observed 的原因分辨(對應完全不同的調整方向)。"""
    if not b.exists:
        return "批次無記錄 → 批次執行異常(daemon 沒跑 / 報錯,疑似系統問題)"
    if b.seeds == 0:
        return "無新 Seed(供給 0)→ 池子乾(該加來源)"
    if b.fetch_failed > 0 and b.fetch_failed >= b.not_found:
        return f"fetch_failed 佔滿({b.fetch_failed} 筆)→ 疑似限流 / DDG 問題"
    if b.not_found > 0:
        return f"not_found 佔滿({b.not_found} 筆)→ 這批 Seed 全數搜不到 domain(市場事實,未必需動作)"
    return "0 observed 但成因不明確,需人工查看"


def evaluate(batches: list[BatchStats]) -> list[Alert]:
    """三警鈴巡檢。回傳觸發的 Alert(可多個,合併推送)。"""
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
                diagnosis=_diagnose_zero_observed(b),
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


def build_message(taiwan_date: str, alerts: list[Alert]) -> str:
    lines = [f"⚠️ [MES 警鈴] {taiwan_date}", ""]
    for i, a in enumerate(alerts, 1):
        lines.append(f"{i}. 偵測:{a.detection}")
        lines.append(f"   最可能原因:{a.diagnosis}")
    lines.append("")
    lines.append("(參考,非自動調整;是否調整由你判斷)")
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
    """巡檢當天三批。有異常才推 Telegram + 記 DB;沒異常安靜。回傳觸發的 Alert。"""
    if taiwan_date is None:
        taiwan_date = datetime.now(_TAIPEI).date().isoformat()
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            batches = await load_today_batches(session, taiwan_date)
            alerts = evaluate(batches)
            if not alerts:
                logger.info("[alarm] %s 無異常,安靜。", taiwan_date)
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
