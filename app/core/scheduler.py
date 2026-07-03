"""APScheduler wiring: cron jobs are built from DB-backed schedule slots.

Slots are user-configurable from the dashboard (time, timezone, market filter,
tickers-per-run, enabled). ``sync_slot_jobs`` reconciles APScheduler with the
slots table and is called at startup and after every schedule edit.

Stock-market slots skip weekends; crypto slots run every day. A run missed
while the machine slept still fires within the hour (misfire_grace_time)
instead of silently skipping the day.
"""

import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.domain import Market
from app.models.base import session_factory
from app.repositories.schedule import ScheduleRepository
from app.services.paper_broker import check_stops
from app.services.pipeline import run_slot

logger = logging.getLogger(__name__)

_SLOT_JOB_PREFIX = "slot_"
_MONITOR_JOB_ID = "stop_monitor"
_MONITOR_INTERVAL_MINUTES = 30


def parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, _, minute_str = value.partition(":")
    hour, minute = int(hour_str), int(minute_str or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid HH:MM time: {value!r}")
    return hour, minute


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Price-only stop-loss tripwire for open positions — no LLM cost, so it
    # runs far more often than the analysis slots and is exempt from the
    # daily run budget.
    scheduler.add_job(
        check_stops,
        "interval",
        minutes=_MONITOR_INTERVAL_MINUTES,
        id=_MONITOR_JOB_ID,
        name="stop-loss monitor",
        coalesce=True,
        max_instances=1,
    )
    return scheduler


async def sync_slot_jobs(scheduler: AsyncIOScheduler) -> None:
    """Reconcile APScheduler jobs with the schedule_slots table."""
    async with session_factory()() as session:
        slots = await ScheduleRepository(session).list_all()

    for job in scheduler.get_jobs():
        if job.id.startswith(_SLOT_JOB_PREFIX):
            job.remove()

    for slot in slots:
        if not slot.enabled:
            continue
        try:
            hour, minute = parse_hhmm(slot.run_time)
            tz = pytz.timezone(slot.timezone)
        except (ValueError, pytz.UnknownTimeZoneError):
            logger.exception("Slot %r has invalid time/timezone; skipping", slot.label)
            continue
        # Equities don't trade weekends; don't burn quota on stale data.
        day_of_week = "*" if slot.market == Market.CRYPTO.value else "mon-fri"
        scheduler.add_job(
            run_slot,
            CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone=tz),
            args=[slot.id],
            id=f"{_SLOT_JOB_PREFIX}{slot.id}",
            name=slot.label,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduled slot %r at %s %s (%s, max %d ticker(s))",
            slot.label, slot.run_time, slot.timezone, day_of_week, slot.max_tickers,
        )
