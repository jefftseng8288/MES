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
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from mes.pipeline import run_daily_batch

# Three fire times a day, in Taiwan time. The CronTrigger's timezone is set EXPLICITLY
# to Asia/Taipei — a pre-built CronTrigger does NOT inherit the scheduler's timezone
# (it defaults to the system-local tz), which previously made hour=2 fire at 02:00
# LOCAL instead of the intended time. Explicit tz removes the trap.
# (observed_at is still stored tz-aware UTC; only the fire times are expressed in TW.)
HARVEST_TZ = "Asia/Taipei"
HARVEST_HOURS = "2,10,21"  # 02:00 / 10:00 / 21:00 Taiwan
HARVEST_MINUTE = 0


async def _job() -> None:
    await run_daily_batch()


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=HARVEST_TZ)
    scheduler.add_job(
        _job,
        CronTrigger(hour=HARVEST_HOURS, minute=HARVEST_MINUTE, timezone=HARVEST_TZ),
        id="daily_harvest",
        max_instances=1,  # never overlap batches
        coalesce=True,  # if we missed a fire, run once, not N times
    )
    return scheduler


async def _run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    print(
        f"[mes.schedule] harvest scheduled at "
        f"{HARVEST_HOURS}:{HARVEST_MINUTE:02d} {HARVEST_TZ} (三批/日)"
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
