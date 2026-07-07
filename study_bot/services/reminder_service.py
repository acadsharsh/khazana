"""Smart reminder service: nudges inactive users up to three times a day."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ReminderStatus, User
from services.user_service import display_name_of
from utils.time_utils import today_utc

logger = logging.getLogger(__name__)

#: Map a reminder slot name to the boolean column it controls.
SLOT_COLUMN = {
    "morning": "morning_sent",
    "afternoon": "afternoon_sent",
    "evening": "evening_sent",
}

REMINDER_MESSAGES = {
    "morning": "☀️ Good morning! You haven't logged any study time yet today. "
    "A quick /log now keeps your streak alive!",
    "afternoon": "🌤️ Half the day is gone - log your study session with /log "
    "and keep the momentum going.",
    "evening": "🌙 Last reminder of the day! Log your hours before midnight so "
    "your streak doesn't reset.",
}


@dataclass
class ReminderResult:
    notified: List[User]
    slot: str


async def _active_users(session: AsyncSession) -> List[User]:
    result = await session.execute(select(User).where(User.is_active.is_(True)))
    return list(result.scalars().all())


async def _logged_today_ids(session: AsyncSession) -> set:
    from models import StudyLog

    result = await session.execute(
        select(StudyLog.telegram_id).where(StudyLog.log_date == today_utc())
    )
    return {row[0] for row in result.all()}


async def _status_for(
    session: AsyncSession, telegram_id: int, day: date
) -> ReminderStatus:
    result = await session.execute(
        select(ReminderStatus).where(
            ReminderStatus.telegram_id == telegram_id,
            ReminderStatus.reminder_date == day,
        )
    )
    status = result.scalar_one_or_none()
    if status is None:
        status = ReminderStatus(
            telegram_id=telegram_id, reminder_date=day
        )
        session.add(status)
    return status


async def mark_logged(session: AsyncSession, telegram_id: int) -> None:
    """Stop further reminders for a user once they log today."""
    status = await _status_for(session, telegram_id, today_utc())
    status.logged_today = True
    status.morning_sent = True
    status.afternoon_sent = True
    status.evening_sent = True


async def users_to_remind(
    session: AsyncSession, slot: str
) -> List[User]:
    """Return users who should receive the *slot* reminder right now."""
    if slot not in SLOT_COLUMN:
        return []
    column = getattr(ReminderStatus, SLOT_COLUMN[slot])
    today = today_utc()
    logged = await _logged_today_ids(session)

    # Users already sent this slot today.
    sent_result = await session.execute(
        select(ReminderStatus.telegram_id).where(
            ReminderStatus.reminder_date == today, column.is_(True)
        )
    )
    already_sent = {row[0] for row in sent_result.all()}

    to_remind: List[User] = []
    for user in await _active_users(session):
        if user.telegram_id in logged:
            continue
        if user.telegram_id in already_sent:
            continue
        # ensure the status row exists & mark slot sent
        status = await _status_for(session, user.telegram_id, today)
        setattr(status, SLOT_COLUMN[slot], True)
        to_remind.append(user)
    return to_remind


async def build_reminder_result(
    session: AsyncSession, slot: str
) -> ReminderResult:
    return ReminderResult(notified=await users_to_remind(session, slot), slot=slot)


async def missing_today(session: AsyncSession) -> List[User]:
    """Users with no study log in the last 24 hours (for the missing task list)."""
    logged = await _logged_today_ids(session)
    return [
        u
        for u in await _active_users(session)
        if u.telegram_id not in logged
    ]
