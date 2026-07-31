"""Daily harvest batch + health report (Phase 1 scheduling layer).

This wraps the EXISTING double-domino write chain (scrape -> infer -> ingest) in a
daily batch runner and an honest health report. It does NOT change the core chain,
three-value semantics, producer/source, Append-Only, or CHECK contracts.

Design intent: the goal is NOT "harvest many stores", it is "run batches steadily,
with every batch's outcome seen honestly". Now three batches/day (02:00/10:00/21:00
Taiwan) to test whether the daily total holds against DDG. The health report reports
THREE proportions separately and never merges them into one "success rate" — collapsing
not_found (a market fact: dead stores) with fetch_failed (us being rate-limited)
would produce a false signal on a batch full of dead seeds. That is "失敗不偽裝"
extended to the health dashboard.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, ObservationLog
from mes.inference import infer_domain
from mes.ingest import (
    FEATURE_INFERRED_DOMAIN,
    ingest_inferred_domain_failure,
    ingest_inferred_domain_success,
    ingest_seed,
)
from mes.jobs import JOB_BASELINE, heartbeat
from mes.normalize import seed_key
from mes.notify import send_telegram
from mes.scrape import SEED_SOURCE_HANDLES, fetch_review_page, parse_store_names

# Taipei tz for batch_id dating — all three of a Taiwan-day's batches (02:00/10:00/
# 21:00 TW) share the same YYYY-MM-DD prefix, numbered -01/-02/-03 in fire order.
_TAIPEI = ZoneInfo("Asia/Taipei")

# batch_size, delay range, and batches/day are all PROVISIONAL values pending real-load
# correction — NOT verified safe baselines. Three batches/day is a gentle way to raise
# the daily total (90 DDG queries) and let real fetch_failed tell us if it holds.
BATCH_SIZE = 30

# Per-seed random delay between inference calls. 20–150s is a DELIBERATELY CONSERVATIVE
# STARTING POINT, not a proven-correct answer — to be tuned after a week of
# fetch_failed data (low all week ->可縮短加快;飆高 -> 拉長或另想辦法).
# RANDOM is a HARD requirement, not a suggestion: a fixed cadence broadcasts "I am a
# bot" and self-induces rate-limiting. Every gap is freshly randomised, and the range
# is deliberately WIDE (20–150, a 130s spread) so the rhythm is irregular / human-like
# and harder to fingerprint. ~85s average x 30 seeds ≈ 42 min/batch (unhurried).
MIN_SEED_DELAY = 20.0
MAX_SEED_DELAY = 150.0

# Between review-page fetches while gathering new names (scraper politeness).
MIN_PAGE_DELAY = 5.0
MAX_PAGE_DELAY = 25.0
# ★ 兩層上限(Jeff 定案 2026-07-31)。背景:原本 MAX_PAGES=12,但實測各來源在第 25 頁
# 都還是滿的(loox 第 40 頁仍有)——「池子乾了」是誤讀,真相是我們自己設了 12 頁的視野
# 邊界,然後把「視野內看完了」當成「市場沒有了」。此誤讀已發生兩次。
#
# MAX_PAGES 刻意設在**實務上正常永遠到不了**的地方 -> 觸頂即為高信度異常訊號(疑似分頁
# bug),不必再判斷是不是正常。設 50/100 反而會常態觸及,訊號充滿雜訊、久了被忽略。
MAX_PAGES = 2000
# 單批蒐集的最長執行時間 —— **實際會生效的煞車**(見 _gather_new_store_names docstring)。
MAX_GATHER_HOURS = 5.0

STOP_TARGET_MET = "target_met"
STOP_MAX_PAGES = "max_pages"
STOP_TIME_LIMIT = "time_limit"
STOP_SOURCES_EXHAUSTED = "sources_exhausted"

_STOP_LABELS = {
    STOP_TARGET_MET: "湊滿目標",
    STOP_MAX_PAGES: f"翻到 {MAX_PAGES} 頁上限",
    STOP_TIME_LIMIT: f"跑滿 {MAX_GATHER_HOURS} 小時上限",
    STOP_SOURCES_EXHAUSTED: "所有來源在該深度都沒有內容(真的翻到底)",
}

LOG_PATH = Path("logs/harvest_health.log")


@dataclass(frozen=True)
class HealthReport:
    """Honest three-proportion health report for one batch (keyed by batch_id)."""

    batch_id: str  # YYYY-MM-DD-NN, which run produced this batch
    requested: int  # how many seeds we wanted (BATCH_SIZE)
    actual: int  # how many we actually processed (may be < requested if Loox ran dry)
    observed: int  # inference succeeded -> a domain
    not_found: int  # inference ran, no trustworthy domain (MARKET fact, not our problem)
    fetch_failed: int  # rate-limited / system couldn't run (THE dial that says "adjust?")
    # ★ 蒐集階段為什麼停 —— 供給不足時據此分辨「我們的視野限制」vs「市場真的沒了」。
    stop_reason: str = STOP_TARGET_MET
    last_page: int = 0
    gather_seconds: float = 0.0

    @classmethod
    def from_statuses(
        cls, batch_id: str, requested: int, statuses: list[str],
        gather: GatherOutcome | None = None,
    ) -> HealthReport:
        return cls(
            batch_id=batch_id,
            requested=requested,
            actual=len(statuses),
            observed=statuses.count("observed"),
            not_found=statuses.count("not_found"),
            fetch_failed=statuses.count("fetch_failed"),
            stop_reason=gather.stop_reason if gather else STOP_TARGET_MET,
            last_page=gather.last_page if gather else 0,
            gather_seconds=gather.elapsed_seconds if gather else 0.0,
        )

    @property
    def hit_cap(self) -> bool:
        return self.stop_reason in (STOP_MAX_PAGES, STOP_TIME_LIMIT)

    def _pct(self, n: int) -> str:
        return f"{n / self.actual * 100:.0f}%" if self.actual else "—"

    def format(self) -> str:
        lines = [
            "===== MES 撈取健康報告 (Harvest Health) =====",
            f"批號 (batch_id): {self.batch_id}",
            f"批次筆數: {self.actual} / 目標 {self.requested}"
            + ("  ⚠️ Loox Seed 供給不足,見說明" if self.actual < self.requested else ""),
            "",
            "三比例(分開呈現,不合併成單一「成功率」):",
            f"  observed     {self.observed:>3}  ({self._pct(self.observed)})"
            "  成功撈到 domain",
            f"  not_found    {self.not_found:>3}  ({self._pct(self.not_found)})"
            "  執行了但搜不到(市場事實/死店,非系統問題)",
            f"  fetch_failed {self.fetch_failed:>3}  ({self._pct(self.fetch_failed)})"
            "  被限流/系統無能 ← 該不該調整節奏的主儀表",
            "",
            "判讀:fetch_failed 比例是判斷『該不該調整節奏』的主要訊號。",
            "      not_found 高只代表這批 Seed 死店多,不代表系統要調整。",
            "      一天三批(-01/-02/-03):比較同日『越晚的批 fetch_failed 是否越高』,",
            "      = 判斷『一天總量是否觸發累積限流』的關鍵訊號。",
        ]
        # ★ 蒐集階段為何停 —— 一眼分辨「視野限制」vs「市場真的沒了」。
        lines.append(
            f"蒐集停止原因: {_STOP_LABELS.get(self.stop_reason, self.stop_reason)}"
            f"(翻到第 {self.last_page} 頁,耗時 {self.gather_seconds / 60:.1f} 分)"
        )
        if self.hit_cap:
            lines.append(
                "  ⚠️ 本批**觸及我們自己設的上限**,不是市場沒有了 —— "
                "供給不足的數字要這樣讀。"
            )
        if self.actual < self.requested:
            lines.append(
                f"      供給不足:所有 Seed 來源只湊到 {self.actual} 個未撈過的新 Store Name"
                f"(要 {self.requested});未重複撈同店湊數。"
            )
        lines.append("=" * 44)
        return "\n".join(lines)


# NN 語義固定:-01/-02/-03 = 三個排程時段(02:00/10:00/21:00 台灣,由 scheduler 傳入
# slot);-04 以上 = 手動 run。手動編號從 4 起(保留 1~3 給排程時段),與 schedule.py 的
# SCHEDULED_SLOTS 的槽數對齊。
FIRST_MANUAL_SEQ = 4


async def _resolve_batch_id(session: AsyncSession, day_str: str, slot: int | None) -> str:
    """Build batch_id 'YYYY-MM-DD-NN' for a Taiwan date.

    slot given (1/2/3, from the scheduler) -> fixed '-0{slot}'. Same-slot re-runs reuse
    the same batch_id (append semantics — the slot's bucket for that day).
    slot None (manual --once) -> next number from 04 up (reserving 1~3 for scheduled slots).
    """
    if slot is not None:
        return f"{day_str}-{slot:02d}"
    rows = await session.execute(
        select(ObservationLog.batch_id)
        .where(ObservationLog.batch_id.like(f"{day_str}-%"))
        .distinct()
    )
    seqs = [int(b.rsplit("-", 1)[1]) for (b,) in rows.all()]
    return f"{day_str}-{max([*seqs, FIRST_MANUAL_SEQ - 1]) + 1:02d}"


@dataclass(frozen=True)
class GatherOutcome:
    """蒐集新 Seed 的結果 + **為什麼停下來** —— 觸頂不可靜默。

    ★ 這是這次修正的核心:過去湊不滿只回報「撈到 2 家」,沒說「我是**翻到上限才停**的」,
    於是「我們的視野限制」與「市場真的沒了」在回報上長得一模一樣 —— 已因此誤判兩次
    (兩次都以為池子乾了,真相都是 MAX_PAGES 擋住)。不管上限設多少,只要觸頂是靜默的,
    同樣的誤判就會再發生。(同一個病:邊界訊號不可偽裝成沒事。)
    """

    names: list[tuple[str, str]]
    stop_reason: str  # target_met / max_pages / time_limit / sources_exhausted
    last_page: int
    elapsed_seconds: float

    @property
    def hit_cap(self) -> bool:
        """是否撞到我們自己設的上限(而非市場真的沒東西)。"""
        return self.stop_reason in (STOP_MAX_PAGES, STOP_TIME_LIMIT)


async def _gather_new_store_names(
    session: AsyncSession, count: int, *, page_sleep: bool
) -> GatherOutcome:
    """Collect up to ``count`` (store_name, app_key) whose Seed does not yet exist.

    Harvests across all Seed sources (SEED_SOURCE_HANDLES), round-robin by page so
    load spreads and fresh supply is found fast. Dedupes within the batch and against
    existing store_name_seed entities (Seed dedupe stays in force — we do NOT re-harvest
    the same store to hit the number). A shortfall (actual < count) is honest signal.

    **兩層上限(Jeff 定案):**
      - `MAX_PAGES=2000` —— 刻意設在實務上正常永遠到不了的地方,所以「觸頂」是**高信度的
        異常訊號**(疑似分頁 bug),不需要判斷。保留它純粹是防失控(萬一分頁永遠回 200)。
      - `MAX_GATHER_HOURS=5` —— **這才是實際會生效的煞車。** 10 來源 × 2000 頁 × 5–25 秒
        ≈ 83 小時,沒有時間煞車一批會跑三天多、卡住後續。且「湊不滿」的正常結局就是一直
        翻下去,所以這不是假想情況。超時**正常收尾**(已撈到的照常回傳,不丟棄)。
    """
    collected: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    started = time.monotonic()
    deadline = started + MAX_GATHER_HOURS * 3600
    page = 0

    def outcome(reason: str) -> GatherOutcome:
        return GatherOutcome(collected, reason, page, time.monotonic() - started)

    for page in range(1, MAX_PAGES + 1):
        progressed = False  # did any app yield names at this page depth?
        for app_key, handle in SEED_SOURCE_HANDLES.items():
            if len(collected) >= count:
                return outcome(STOP_TARGET_MET)
            if time.monotonic() > deadline:
                return outcome(STOP_TIME_LIMIT)
            try:
                html = fetch_review_page(handle, page)
            except httpx.HTTPError:
                continue
            names = parse_store_names(html)
            if names:
                progressed = True
            for name in names:
                key = seed_key(name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                exists = await session.scalar(
                    select(Entity.entity_id).where(
                        Entity.entity_type == "store_name_seed", Entity.canonical_key == key
                    )
                )
                if exists is None:
                    collected.append((name, app_key))
                    if len(collected) >= count:
                        return outcome(STOP_TARGET_MET)
            if page_sleep:
                time.sleep(random.uniform(MIN_PAGE_DELAY, MAX_PAGE_DELAY))
        if not progressed:
            # 所有來源在這個深度都沒內容 = 真的翻到底(這才是「市場沒了」的正當訊號)。
            return outcome(STOP_SOURCES_EXHAUSTED)
    return outcome(STOP_MAX_PAGES)  # 跑完 2000 頁仍沒湊滿 -> 疑似分頁 bug


def build_cap_hit_message(report: HealthReport) -> str:
    """觸頂訊息 —— **必須說清楚是哪一種**,兩者是不同狀況、修法不同。

    沿用警鈴「帶原因診斷」的精神:不只說「觸頂了」,要說翻到第幾頁、撈到幾家、耗時多久。
    """
    if report.stop_reason == STOP_MAX_PAGES:
        headline = f"翻到 {MAX_PAGES} 頁上限仍未湊滿"
        diagnosis = (
            "疑似分頁有 bug(永遠回 200 但內容重複),或該來源真的深不見底。"
            "正常情況不該到得了這個上限 —— 這是高信度的異常訊號。"
        )
    else:
        headline = f"跑滿 {MAX_GATHER_HOURS} 小時上限"
        diagnosis = (
            "執行時間耗盡:可能是深度不足以湊滿目標(一直往下翻),或翻頁間隔過長。"
            "已撈到的照常寫入,未丟棄。"
        )
    return "\n".join([
        f"⏱️ [MES 蒐集觸頂] {report.batch_id}",
        "",
        f"偵測:{headline}",
        f"  翻到第 {report.last_page} 頁 · 撈到 {report.actual}/{report.requested} 家"
        f" · 耗時 {report.gather_seconds / 3600:.2f} 小時",
        f"  來源數:{len(SEED_SOURCE_HANDLES)}",
        "",
        f"最可能原因:{diagnosis}",
        "",
        "(參考,非自動調整;是否調整由你判斷)",
    ])


async def run_daily_batch(
    *,
    slot: int | None = None,
    batch_size: int = BATCH_SIZE,
    min_delay: float = MIN_SEED_DELAY,
    max_delay: float = MAX_SEED_DELAY,
    seed_sleep: bool = True,
    page_sleep: bool = True,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    emit: bool = True,
) -> HealthReport:
    """Run one batch through the existing chain and return an honest report.

    ``slot`` = the scheduled slot (1/2/3 for 02:00/10:00/21:00 Taiwan), passed by the
    scheduler; None = a manual run (batch_id numbered from -04 up). For each new Store
    Name: ingest_seed (骨牌一) -> infer_domain -> ingest success/failure (骨牌二). A fresh
    20–150s random sleep separates seeds (production default).
    """
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    statuses: list[str] = []
    batch_id = ""
    try:
        async with heartbeat(JOB_BASELINE) as beat, session_maker() as session:
            day_str = datetime.now(_TAIPEI).date().isoformat()
            batch_id = await _resolve_batch_id(session, day_str, slot)
            gather = await _gather_new_store_names(
                session, batch_size, page_sleep=page_sleep
            )
            names = gather.names
            for i, (name, app_key) in enumerate(names):
                seed = await ingest_seed(
                    session, name, batch_id=batch_id, source_page_label=f"{app_key} review page"
                )
                await session.commit()
                result = infer_domain(name)
                if result.status == "observed" and result.domain and result.raw_url:
                    await ingest_inferred_domain_success(
                        session, seed, raw_url=result.raw_url, domain=result.domain,
                        producer=result.producer, batch_id=batch_id,
                    )
                else:
                    await ingest_inferred_domain_failure(
                        session, seed, status=result.status,
                        producer=result.producer, batch_id=batch_id,
                    )
                await session.commit()
                statuses.append(result.status)
                if seed_sleep and i < len(names) - 1:
                    time.sleep(random.uniform(min_delay, max_delay))
            # 心跳摘要直接沿用既有 HealthReport 的數字,不另記一份(避免兩套並行)。
            beat.summary = {
                "batch_id": batch_id, "requested": batch_size, "actual": len(statuses),
                "observed": statuses.count("observed"),
                "not_found": statuses.count("not_found"),
                "fetch_failed": statuses.count("fetch_failed"),
                "stop_reason": gather.stop_reason, "last_page": gather.last_page,
                "hit_cap": gather.hit_cap,
            }
    finally:
        if engine is not None:
            await engine.dispose()

    report = HealthReport.from_statuses(batch_id, batch_size, statuses, gather)
    if emit:
        _emit(report)
        # ★ 觸頂不可靜默 —— 主動回報,並說清楚是哪一種上限。
        if report.hit_cap:
            send_telegram(build_cap_hit_message(report))
    return report


def _emit(report: HealthReport) -> None:
    """Print the report and append it to the harvest log for next-day review."""
    text = report.format()
    print(text)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")


async def compute_health_for_batch(session: AsyncSession, batch_id: str) -> HealthReport:
    """Re-derive a batch's health report from the DB by batch_id (for review/comparison).

    To compare a Taiwan-day's three batches, call this for '<date>-01', '-02', '-03'.
    """
    rows = await session.execute(
        select(ObservationLog.status, func.count())
        .where(
            ObservationLog.feature == FEATURE_INFERRED_DOMAIN,
            ObservationLog.batch_id == batch_id,
        )
        .group_by(ObservationLog.status)
    )
    counts = {status: n for status, n in rows.all()}
    total = sum(counts.values())
    return HealthReport(
        batch_id=batch_id,
        requested=total,
        actual=total,
        observed=counts.get("observed", 0),
        not_found=counts.get("not_found", 0),
        fetch_failed=counts.get("fetch_failed", 0),
    )
