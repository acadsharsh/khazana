"""APScheduler integration (AsyncIOScheduler).

Defines every recurring job:

* Daily report  - every day at ``REPORT_TIME`` (default 02:30 UTC / 08:00 IST)
* Weekly report - every Sunday
* Monthly report- on the 1st of each month
* Smart reminders - morning / afternoon / evening for inactive users
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import AsyncSessionLocal, session_scope
from handlers.partners import notify_partners_of_missing
from reports.report_builder import (
    build_daily_report,
    build_monthly_report,
    build_weekly_report,
)
from services.reminder_service import REMINDER_MESSAGES, build_reminder_result
from utils.time_utils import get_zoneinfo, parse_time_pair

logger = logging.getLogger("scheduler")

#: Maps the n-th configured reminder time to a reminder slot name.
SLOT_BY_INDEX = {0: "morning", 1: "afternoon", 2: "evening"}

#: Holds the running Telegram ``Bot`` instance for use by scheduled jobs.
_bot = None


def set_bot(bot) -> None:
    """Capture the bot instance so jobs can send messages."""
    global _bot
    _bot = bot


def get_bot():
    """Return the captured bot instance (or ``None``)."""
    return _bot


def _tz():
    return get_zoneinfo(settings.timezone)


async def _send_to_group(bot, text: str, pin: bool = False) -> None:
    """Send a (optionally pinned) HTML message to the configured group."""
    if not settings.group_id or bot is None:
        logger.warning("GROUP_ID not configured; skipping scheduled send.")
        return
    try:
        message = await bot.send_message(
            chat_id=settings.group_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if pin:
            try:
                await bot.pin_chat_message(
                    chat_id=settings.group_id,
                    message_id=message.message_id,
                    disable_notification=True,
                )
            except Exception:  # pragma: no cover - pinning requires admin rights
                logger.info("Could not pin message (need admin rights in the group).")
    except Exception:  # pragma: no cover
        logger.exception("Failed to send a scheduled message to the group")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
async def daily_report_job() -> None:
    """Generate & post the daily study report, then notify accountability partners."""
    bot = get_bot()
    try:
        async with session_scope() as session:
            text = await build_daily_report(session)
        await _send_to_group(bot, text, pin=True)
        await notify_partners_of_missing(bot)
    except Exception:  # pragma: no cover - jobs must never crash the scheduler
        logger.exception("daily_report_job failed")


async def weekly_report_job() -> None:
    bot = get_bot()
    try:
        async with session_scope() as session:
            text = await build_weekly_report(session)
        await _send_to_group(bot, text, pin=True)
    except Exception:  # pragma: no cover
        logger.exception("weekly_report_job failed")


async def monthly_report_job() -> None:
    bot = get_bot()
    try:
        async with session_scope() as session:
            text = await build_monthly_report(session)
        await _send_to_group(bot, text, pin=True)
    except Exception:  # pragma: no cover
        logger.exception("monthly_report_job failed")


async def reminder_job(slot: str) -> None:
    """Nudge every inactive user for the given *slot* (morning/afternoon/evening)."""
    bot = get_bot()
    try:
        async with session_scope() as session:
            result = await build_reminder_result(session, slot)
        message = REMINDER_MESSAGES.get(slot, "Don't forget to log your study time today! 📚")
        sent = 0
        for user in result.notified:
            try:
                await bot.send_message(chat_id=user.telegram_id, text=message)
                sent += 1
            except Exception:  # pragma: no cover - user may have blocked the bot
                logger.debug("Could not send reminder to %s", user.telegram_id)
        logger.info("Sent %d '%s' reminders", sent, slot)
    except Exception:  # pragma: no cover
        logger.exception("reminder_job failed")


class SchedulerManager:
    """Owns the :class:`AsyncIOScheduler` and wires up every recurring job."""

    def __init__(self, application) -> None:
        self.application = application
        self.bot = application.bot
        self.scheduler = AsyncIOScheduler(timezone=_tz())

    # ------------------------------------------------------------------
    def setup(self) -> None:
        """Register all cron jobs (does not start the scheduler)."""
        tz = _tz()
        set_bot(self.bot)

        # Daily report at REPORT_TIME.
        self.scheduler.add_job(
            daily_report_job,
            CronTrigger(
                hour=settings.report_hour,
                minute=settings.report_minute,
                timezone=tz,
            ),
            id="daily_report",
            replace_existing=True,
        )

        # Weekly report every Sunday, shortly after the daily report.
        self.scheduler.add_job(
            weekly_report_job,
            CronTrigger(day_of_week="sun", hour=settings.report_hour, minute=45, timezone=tz),
            id="weekly_report",
            replace_existing=True,
        )

        # Monthly report on the 1st.
        self.scheduler.add_job(
            monthly_report_job,
            CronTrigger(day=1, hour=settings.report_hour, minute=15, timezone=tz),
            id="monthly_report",
            replace_existing=True,
        )

        # Smart reminders.
        for index, time_str in enumerate(settings.reminder_times[:3]):
            slot = SLOT_BY_INDEX.get(index)
            if not slot:
                continue
            try:
                hour, minute = parse_time_pair(time_str)
            except (ValueError, IndexError):
                logger.warning("Skipping invalid reminder time '%s'", time_str)
                continue
            self.scheduler.add_job(
                reminder_job,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                args=[slot],
                id=f"reminder_{slot}",
                replace_existing=True,
            )
        logger.info("Scheduler configured with %d jobs", len(self.scheduler.get_jobs()))

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")


async def load_runtime_overrides() -> None:
    """Reload persisted runtime settings (e.g. focus_threshold) on startup."""
    from models import Setting
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "focus_threshold")
        )
        setting = result.scalar_one_or_none()
    if setting and setting.value:
        try:
            import config

            config.set_runtime("focus_threshold", float(setting.value))
            logger.info("Loaded focus_threshold override: %s", setting.value)
        except (TypeError, ValueError):  # pragma: no cover
            pass
