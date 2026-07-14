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
#
# Taiwan hour -> fixed slot number. The slot becomes the batch_id suffix: -01/-02/-03
# ALWAYS mean these three scheduled times (a batch_id tells you which slot). Manual
# --once runs are numbered from -04 up (see pipeline.FIRST_MANUAL_SEQ). Keep this in
# sync with pipeline.FIRST_MANUAL_SEQ (= len(SCHEDULED_SLOTS) + 1).
HARVEST_TZ = "Asia/Taipei"
HARVEST_MINUTE = 0
SCHEDULED_SLOTS = {2: 1, 10: 2, 21: 3}  # 02:00 -> 01, 10:00 -> 02, 21:00 -> 03


async def _job(slot: int) -> None:
    await run_daily_batch(slot=slot)


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
    return scheduler


async def _run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    times = ", ".join(f"{h:02d}:{HARVEST_MINUTE:02d}->-{s:02d}" for h, s in SCHEDULED_SLOTS.items())
    print(f"[mes.schedule] harvest scheduled {HARVEST_TZ}: {times} (三批/日)")
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
