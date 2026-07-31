"""APScheduler daemon: run the harvest batch three times a day, unattended.

Usage:
    uv run python -m mes.schedule          # run the daemon (fires at HARVEST_HOURS)
    uv run python -m mes.schedule --once   # trigger one batch now and exit (manual test)

Three batches/day (02:00 / 10:00 / 21:00 Taiwan) spread across the day (gaps of
8h / 11h / 5h) so DDG gets breathing room between them — this tests the DAILY TOTAL
(3 x 30 = 90 queries), not a short burst. Each fire runs one run_daily_batch.

First version deliberately does NO auto-alert / auto-backoff: we don't yet have
enough real cases to define "how much to adjust, and how". Accumulate real
fetch_failed data points first, then Jeff decides by eye. (先有 Observation 再演化。)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from mes.harvest import run_store_harvest_batch
from mes.pipeline import run_daily_batch

logger = logging.getLogger(__name__)

# Three fire times a day, in Taiwan time. The CronTrigger's timezone is set EXPLICITLY
# to Asia/Taipei — a pre-built CronTrigger does NOT inherit the scheduler's timezone
# (it defaults to the system-local tz), which previously made hour=2 fire at 02:00
# LOCAL instead of the intended time. Explicit tz removes the trap.
# (observed_at is still stored tz-aware UTC; only the fire times are expressed in TW.)
#
# Taiwan hour -> fixed slot number. The slot becomes the batch_id suffix: -01/-02/-03
# ALWAYS mean these three scheduled times (a batch_id tells you which slot). Manual
# --once runs are numbered from -04 up (see pipeline.FIRST_MANUAL_SEQ). Keep this in
# sync with pipeline.FIRST_MANUAL_SEQ (= len(SCHEDULED_SLOTS) + 1).
HARVEST_TZ = "Asia/Taipei"
HARVEST_MINUTE = 0
SCHEDULED_SLOTS = {2: 1, 10: 2, 21: 3}  # baseline (DDG): 02:00 -> 01, 10:00 -> 02, 21:00 -> 03

# Store feature-harvest (Phase 1-D): INDEPENDENT schedule. Pokes each store's own server
# (products.json + homepage) — a different target from DDG, independent rate-limiting.
# Every 3h in Taiwan time (00/03/06/09/12/15/18/21) ≈ 8 batches/day × 1–3 stores.
STORE_HARVEST_HOURS = "*/3"


# ★ 跨 slot 互斥鎖(2026-07-31)。
# APScheduler 的 `max_instances` 是 **per-job**,而三個 baseline slot 是三個獨立 job
# (harvest_slot_1/2/3)—— 它只擋「同一個 slot 疊自己」,**擋不住 slot 3(21:00)還在跑時
# slot 1(02:00)啟動**。而 21:00→02:00 剛好 5 小時,與蒐集的時間煞車
# (pipeline.MAX_GATHER_HOURS)等長 → 只要有一批跑滿煞車,就必然首尾相接。
#
# 資料不會壞(batch_id 不同 + Append-Only),但**兩批同時對 DuckDuckGo 發請求會讓速率
# 翻倍**,正好打在 baseline 最敏感的地方(DDG 限流)。故三個 slot 共用這把鎖。
#
# 選擇「等待」而非「跳過」:等待保住該批(不損失供給),且因為時間煞車有界、slot 間隔
# 5–11 小時,不會累積成堆。等待本身會記 WARNING;且會觸發等待的前提是前一批跑滿煞車,
# 那條路徑已經會主動推 Telegram 觸頂訊息,所以不是靜默的。
_BASELINE_LOCK = asyncio.Lock()


async def _job(slot: int) -> None:
    if _BASELINE_LOCK.locked():
        logger.warning(
            "[schedule] slot %d 觸發時上一批 baseline 仍在執行 —— 等待中(避免同時打 DDG "
            "讓速率翻倍)。前一批很可能跑滿了蒐集時間煞車。", slot,
        )
    async with _BASELINE_LOCK:
        await run_daily_batch(slot=slot)


async def _store_harvest_job() -> None:
    await run_store_harvest_batch()


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=HARVEST_TZ)
    for hour, slot in SCHEDULED_SLOTS.items():
        scheduler.add_job(
            _job,
            CronTrigger(hour=hour, minute=HARVEST_MINUTE, timezone=HARVEST_TZ),
            id=f"harvest_slot_{slot}",
            args=[slot],
            max_instances=1,  # never overlap the same slot
            coalesce=True,  # if we missed a fire, run once, not N times
        )
    scheduler.add_job(
        _store_harvest_job,
        CronTrigger(hour=STORE_HARVEST_HOURS, minute=HARVEST_MINUTE, timezone=HARVEST_TZ),
        id="store_harvest",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def _run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    times = ", ".join(f"{h:02d}:{HARVEST_MINUTE:02d}->-{s:02d}" for h, s in SCHEDULED_SLOTS.items())
    print(
        f"[mes.schedule] baseline(DDG) {HARVEST_TZ}: {times} (三批/日) | "
        f"store-harvest: every {STORE_HARVEST_HOURS}h (獨立鏈路)"
    )
    try:
        await asyncio.Event().wait()  # keep the event loop alive
    finally:
        scheduler.shutdown()


def main() -> None:
    if "--once" in sys.argv:
        asyncio.run(run_daily_batch())
    else:
        asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
