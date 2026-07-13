"""APScheduler daemon: run the daily harvest batch once per day, unattended.

Usage:
    uv run python -m mes.schedule          # run the daemon (fires daily at HARVEST_HOUR)
    uv run python -m mes.schedule --once   # trigger one batch now and exit (manual test)

First version deliberately does NO auto-alert / auto-backoff: we don't yet have
enough real cases to define "how much to adjust, and how". Accumulate a week of
fetch_failed data points first, then Jeff decides by eye. (先有 Observation 再演化。)
"""

from __future__ import annotations

import asyncio
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from mes.pipeline import run_daily_batch

# One batch a day at 02:00 UTC = 10:00 Taiwan time (UTC+8). Scheduler runs in UTC
# to match observed_at (stored tz-aware UTC); 02:00 UTC is the fire time.
HARVEST_HOUR = 2
HARVEST_MINUTE = 0


async def _job() -> None:
    await run_daily_batch()


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _job,
        CronTrigger(hour=HARVEST_HOUR, minute=HARVEST_MINUTE),
        id="daily_harvest",
        max_instances=1,  # never overlap batches
        coalesce=True,  # if we missed a fire, run once, not N times
    )
    return scheduler


async def _run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    print(
        f"[mes.schedule] daily harvest scheduled at "
        f"{HARVEST_HOUR:02d}:{HARVEST_MINUTE:02d} UTC (= 10:00 Taiwan)"
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
