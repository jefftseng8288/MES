"""Daily harvest batch + health report (Phase 1 scheduling layer).

This wraps the EXISTING double-domino write chain (scrape -> infer -> ingest) in a
daily batch runner and an honest health report. It does NOT change the core chain,
three-value semantics, producer/source, Append-Only, or CHECK contracts.

Design intent: the goal is NOT "harvest many stores", it is "run one batch a day,
steadily, with every batch's outcome seen honestly". The health report reports THREE
proportions separately and never merges them into one "success rate" — collapsing
not_found (a market fact: dead stores) with fetch_failed (us being rate-limited)
would produce a false signal on a batch full of dead seeds. That is "失敗不偽裝"
extended to the health dashboard.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import Date, cast, func, select
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
from mes.normalize import seed_key
from mes.scrape import fetch_review_page, parse_store_names

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
MAX_PAGES = 12  # safety cap while gathering new Loox names

LOG_PATH = Path("logs/harvest_health.log")


@dataclass(frozen=True)
class HealthReport:
    """Honest three-proportion health report for one daily batch."""

    day: date
    requested: int  # how many seeds we wanted (BATCH_SIZE)
    actual: int  # how many we actually processed (may be < requested if Loox ran dry)
    observed: int  # inference succeeded -> a domain
    not_found: int  # inference ran, no trustworthy domain (MARKET fact, not our problem)
    fetch_failed: int  # rate-limited / system couldn't run (THE dial that says "adjust?")

    @classmethod
    def from_statuses(cls, day: date, requested: int, statuses: list[str]) -> HealthReport:
        return cls(
            day=day,
            requested=requested,
            actual=len(statuses),
            observed=statuses.count("observed"),
            not_found=statuses.count("not_found"),
            fetch_failed=statuses.count("fetch_failed"),
        )

    def _pct(self, n: int) -> str:
        return f"{n / self.actual * 100:.0f}%" if self.actual else "—"

    def format(self) -> str:
        lines = [
            "===== MES 撈取健康報告 (Harvest Health) =====",
            f"日期 (observed_at date): {self.day.isoformat()}",
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
        ]
        if self.actual < self.requested:
            lines.append(
                f"      供給不足:Loox 只湊到 {self.actual} 個未撈過的新 Store Name"
                f"(要 {self.requested});未重複撈同店湊數。"
                "\n      這是真實資訊(評論頁翻到底 / 新店供給有限)。"
            )
        lines.append("=" * 44)
        return "\n".join(lines)


async def _gather_new_store_names(
    session: AsyncSession, count: int, *, page_sleep: bool
) -> list[str]:
    """Collect up to ``count`` Store Names from Loox whose Seed does not yet exist.

    Dedupes within the batch and against existing store_name_seed entities (Seed
    dedupe stays in force — we do NOT re-harvest the same store to hit the number).
    Stops early if a page yields no names (ran out / structure changed) — that
    shortfall is itself honest signal, reported via HealthReport.actual < requested.
    """
    collected: list[str] = []
    seen_keys: set[str] = set()
    page = 1
    while len(collected) < count and page <= MAX_PAGES:
        try:
            html = fetch_review_page("loox", page)
        except httpx.HTTPError:
            break
        names = parse_store_names(html)
        if not names:
            break
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
                collected.append(name)
                if len(collected) >= count:
                    break
        page += 1
        if page_sleep and len(collected) < count:
            time.sleep(random.uniform(MIN_PAGE_DELAY, MAX_PAGE_DELAY))
    return collected


async def run_daily_batch(
    *,
    batch_size: int = BATCH_SIZE,
    min_delay: float = MIN_SEED_DELAY,
    max_delay: float = MAX_SEED_DELAY,
    seed_sleep: bool = True,
    page_sleep: bool = True,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    emit: bool = True,
) -> HealthReport:
    """Run one daily batch through the existing chain and return an honest report.

    For each new Store Name: ingest_seed (骨牌一) -> infer_domain -> ingest success or
    failure (骨牌二). A fresh 20–150s random sleep separates seeds (production default).
    """
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    statuses: list[str] = []
    try:
        async with session_maker() as session:
            names = await _gather_new_store_names(session, batch_size, page_sleep=page_sleep)
            for i, name in enumerate(names):
                seed = await ingest_seed(session, name)
                await session.commit()
                result = infer_domain(name)
                if result.status == "observed" and result.domain and result.raw_url:
                    await ingest_inferred_domain_success(
                        session, seed, raw_url=result.raw_url, domain=result.domain,
                        producer=result.producer,
                    )
                else:
                    await ingest_inferred_domain_failure(
                        session, seed, status=result.status, producer=result.producer
                    )
                await session.commit()
                statuses.append(result.status)
                if seed_sleep and i < len(names) - 1:
                    time.sleep(random.uniform(min_delay, max_delay))
    finally:
        if engine is not None:
            await engine.dispose()

    report = HealthReport.from_statuses(datetime.now(UTC).date(), batch_size, statuses)
    if emit:
        _emit(report)
    return report


def _emit(report: HealthReport) -> None:
    """Print the report and append it to the harvest log for next-day review."""
    text = report.format()
    print(text)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")


async def compute_health_for_date(session: AsyncSession, day: date) -> HealthReport:
    """Re-derive a day's health report from the DB (batch = observed_at date).

    Used for next-day review of a past batch. In production one batch runs per day,
    so this equals that day's batch.
    """
    rows = await session.execute(
        select(ObservationLog.status, func.count())
        .where(
            ObservationLog.feature == FEATURE_INFERRED_DOMAIN,
            cast(ObservationLog.observed_at, Date) == day,
        )
        .group_by(ObservationLog.status)
    )
    counts = {status: n for status, n in rows.all()}
    total = sum(counts.values())
    return HealthReport(
        day=day,
        requested=total,
        actual=total,
        observed=counts.get("observed", 0),
        not_found=counts.get("not_found", 0),
        fetch_failed=counts.get("fetch_failed", 0),
    )
