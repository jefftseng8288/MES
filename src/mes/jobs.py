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
import subprocess
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import JobRunLog

logger = logging.getLogger(__name__)

def _resolve_code_version() -> str | None:
    """執行當下**已載入的**程式碼版本(git HEAD 短 hash)。

    ★ 必須在 **import 時**求值,不可延後到執行時才問 —— 這是整個機制的關鍵:
    常駐 daemon 的程式碼凍結在**程序啟動當下**,但 `git rev-parse` 讀的是**磁碟現況**。
    若在執行時才問,一個跑著舊 code 的 daemon 會回報「磁碟上的新 hash」,反而掩蓋問題。
    在 import 時求值,才會誠實凍結成「我啟動時載入的那版」。

    ★★ `--dirty`:工作樹有未 commit 的改動時,hash 會標成 `abc1234-dirty`。
    **這不是裝飾,是誠實性的必要條件。** 沒有它,daemon 明明跑著「含未 commit 改動的 code」,
    卻回報一個乾淨的 commit hash —— 又是一個「看起來對、其實不對」的訊號,而且**正好在最需要
    它的場景失真**(開發中、剛改完還沒 commit,恰恰是最容易搞混跑的是哪版的時候)。
    2026-08-02 實際發生過:重啟時工作樹有未 commit 的修正,hash 卻標成上一個 commit。

    註:`--dirty` 只看**已追蹤檔案**的修改(git 標準語義),新增的未追蹤檔不算。
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--abbrev=7"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# 在 import 時凍結 —— 見上方 docstring,順序不可改。
CODE_VERSION = _resolve_code_version()

# job_run_log.status 的受控值(不下沉 DB CHECK —— 同 alert_type,利擴充)。
#   success / failed = 真的跑了;missed = **排程把它丟棄了,根本沒跑**(見 record_missed)。
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_MISSED = "missed"
# 真的執行過的狀態 —— 判斷「有沒有跑」時只能算這兩種,missed 不算。
RAN_STATUSES = (STATUS_SUCCESS, STATUS_FAILED)

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
            # 自動帶上「這一次是哪版 code 跑的」—— 讓「跑的是不是最新 code」可觀測,
            # 不必靠推理(2026-08-01:harvest 常駐 daemon 跑了 16 天舊 code 才被發現)。
            session.add(JobRunLog(
                job=job, started_at=started_at, finished_at=datetime.now(UTC),
                status=status, summary={**(summary or {}), "code_version": CODE_VERSION},
                error=error,
            ))
            await session.commit()
    except Exception:  # noqa: BLE001 - 心跳失敗不該再把主流程壓垮,但要留痕
        logger.exception("[heartbeat] 寫入 %s 心跳失敗", job)
    finally:
        if engine is not None:
            await engine.dispose()


async def record_missed(
    job: str,
    scheduled_run_time: datetime,
    detail: dict[str, Any] | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """記錄一次「**被排程丟棄**」—— 任務根本沒跑,不是跑了失敗。

    ★ 為什麼要獨立記:APScheduler 的 `misfire_grace_time` 一過就**整個丟棄**該次任務,
    只在 stderr 留一行字。若不記進 DB,警鈴只看得到「沒有心跳」,診斷會說「daemon 沒跑 /
    報錯 / 沒 load」—— 全都不對,真相是「排程丟棄了它」。**第四種原因,修法也完全不同**
    (要調 misfire_grace_time,不是去救 daemon)。

    status 記 `missed` 而非 success/failed,因為它**沒有執行** ——
    判斷「有沒有跑」時必須排除它(見 `RAN_STATUSES`),否則會把「被丟棄」誤當成「有跑」。
    """
    await record_run(
        job, scheduled_run_time, STATUS_MISSED,
        {**(detail or {}), "scheduled_run_time": scheduled_run_time.isoformat()},
        error="APScheduler misfire:超過 misfire_grace_time,該次任務被丟棄(未執行)",
        session_maker=session_maker,
    )


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
