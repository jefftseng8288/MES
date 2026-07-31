"""各鏈路的心跳上報(per-run heartbeat)。

**定位:** 每個 daemon 跑完就記一筆 `job_run_log`,**即使產出為 0 也要記** ——
「沒產出」不等於「沒跑」,而這兩者的修法完全不同(見 `JobRunLog` docstring)。

用法(不改動各鏈路核心邏輯,只在收尾包一層):

    async with heartbeat(JOB_PROJECTION) as beat:
        rows = await do_the_work()
        beat.summary["rows"] = rows      # 產出摘要,供警鈴判斷正常/異常

例外會被記成 `status='failed'` + error 後**原樣拋出**(不吞例外:daemon 該掛還是要掛,
但至少留下「跑了、失敗了」的證據)。
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import JobRunLog

logger = logging.getLogger(__name__)

JOB_BASELINE = "baseline"
JOB_HARVEST = "harvest"
JOB_PROJECTION = "projection"
JOB_INSIGHT = "insight"
ALL_JOBS = (JOB_BASELINE, JOB_HARVEST, JOB_PROJECTION, JOB_INSIGHT)

# 各 job 的預期執行頻率(僅供人讀 / 每日安好顯示;警鈴用的是下面的寬限窗)。
EXPECTED_RUNS_PER_DAY = {JOB_BASELINE: 3, JOB_HARVEST: 8, JOB_PROJECTION: 1, JOB_INSIGHT: 1}


@dataclass
class Beat:
    """一次執行的心跳載體;工作過程往 `summary` 塞產出摘要。"""

    job: str
    started_at: datetime
    summary: dict[str, Any] = field(default_factory=dict)


async def record_run(
    job: str,
    started_at: datetime,
    status: str,
    summary: dict[str, Any] | None,
    error: str | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """寫一筆心跳。**用自己的連線** —— 工作用的 session 在例外後可能已不可用。"""
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            session.add(JobRunLog(
                job=job, started_at=started_at, finished_at=datetime.now(UTC),
                status=status, summary=summary or {}, error=error,
            ))
            await session.commit()
    except Exception:  # noqa: BLE001 - 心跳失敗不該再把主流程壓垮,但要留痕
        logger.exception("[heartbeat] 寫入 %s 心跳失敗", job)
    finally:
        if engine is not None:
            await engine.dispose()


@asynccontextmanager
async def heartbeat(
    job: str, *, session_maker: async_sessionmaker[AsyncSession] | None = None
) -> AsyncIterator[Beat]:
    """包住一次執行:正常結束記 success,拋例外記 failed + error 後原樣拋出。"""
    beat = Beat(job=job, started_at=datetime.now(UTC))
    try:
        yield beat
    except Exception as exc:
        await record_run(
            job, beat.started_at, "failed", beat.summary,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}",
            session_maker=session_maker,
        )
        raise
    else:
        await record_run(
            job, beat.started_at, "success", beat.summary, session_maker=session_maker
        )
