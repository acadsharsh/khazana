"""Study session logging service.

Responsibilities:

* Create :class:`StudyLog` rows with full calendar metadata.
* Enforce business rules (max hours/day, duplicate detection).
* Maintain streaks, totals, XP and daily-goal progress.
* Provide hour-aggregation queries used by reports/analytics.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import DailyGoal, StudyLog, User
from utils.helpers import xp_for_hours
from utils.time_utils import (
    days_ago,
    last_n_days,
    month_of,
    start_of_week,
    today_utc,
    week_number,
    year_of,
)

logger = logging.getLogger(__name__)


@dataclass
class LogResult:
    """The outcome of a successful study log."""

    log: StudyLog
    xp_earned: int
    leveled_up: bool
    new_level: int
    goal_completed: bool


class StudyServiceError(Exception):
    """Raised for business-rule violations when logging study time."""


# ---------------------------------------------------------------------------
# Hour aggregation queries
# ---------------------------------------------------------------------------
async def hours_on(session: AsyncSession, telegram_id: int, day) -> float:
    """Total hours logged by a user on a specific date."""
    result = await session.execute(
        select(func.coalesce(func.sum(StudyLog.hours), 0.0)).where(
            StudyLog.telegram_id == telegram_id, StudyLog.log_date == day
        )
    )
    return float(result.scalar_one())


async def hours_today(session: AsyncSession, telegram_id: int) -> float:
    return await hours_on(session, telegram_id, today_utc())


async def hours_in_range(
    session: AsyncSession, telegram_id: int, start, end
) -> float:
    """Total hours between two dates inclusive."""
    result = await session.execute(
        select(func.coalesce(func.sum(StudyLog.hours), 0.0)).where(
            StudyLog.telegram_id == telegram_id,
            StudyLog.log_date >= start,
            StudyLog.log_date <= end,
        )
    )
    return float(result.scalar_one())


async def hours_this_week(session: AsyncSession, telegram_id: int) -> float:
    week_start = start_of_week(today_utc())
    return await hours_in_range(session, telegram_id, week_start, today_utc())


async def hours_this_month(session: AsyncSession, telegram_id: int) -> float:
    today = today_utc()
    month_start = today.replace(day=1)
    return await hours_in_range(session, telegram_id, month_start, today)


async def distinct_study_days(
    session: AsyncSession, telegram_id: int, days: int = 30
) -> int:
    """Count of distinct days with at least one log in the last *days*."""
    window = last_n_days(today_utc(), days)
    result = await session.execute(
        select(func.count(func.distinct(StudyLog.log_date))).where(
            StudyLog.telegram_id == telegram_id,
            StudyLog.log_date >= window[0],
        )
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
async def _looks_like_duplicate(
    session: AsyncSession, telegram_id: int, subject: str, hours: float
) -> bool:
    """Detect an accidental re-submission within a short window."""
    threshold = datetime.utcnow() - timedelta(minutes=settings.duplicate_window_minutes)
    result = await session.execute(
        select(StudyLog).where(
            StudyLog.telegram_id == telegram_id,
            StudyLog.subject == subject,
            StudyLog.hours == hours,
            StudyLog.timestamp >= threshold,
        )
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
async def log_study(
    session: AsyncSession,
    user: User,
    subject: str,
    hours: float,
    note: str,
) -> LogResult:
    """Record a study session and update every derived stat."""
    telegram_id = user.telegram_id

    if await _looks_like_duplicate(session, telegram_id, subject, hours):
        raise StudyServiceError(
            "That looks like a duplicate of a log you just submitted. "
            "Use /undo if you made a mistake."
        )

    daily_total = await hours_today(session, telegram_id)
    if daily_total + hours > settings.max_hours_per_day:
        raise StudyServiceError(
            f"You've already logged {daily_total:g}h today and the daily cap is "
            f"{settings.max_hours_per_day:g}h."
        )
    if daily_total > 0 and daily_total + hours > settings.max_hours_per_day:
        raise StudyServiceError("That would exceed the daily limit.")

    today = today_utc()
    xp_earned = xp_for_hours(hours)
    old_level = user.level

    log = StudyLog(
        telegram_id=telegram_id,
        username=user.username,
        subject=subject,
        hours=hours,
        note=note or None,
        timestamp=datetime.utcnow(),
        log_date=today,
        week_number=week_number(today),
        month=month_of(today),
        year=year_of(today),
        xp_earned=xp_earned,
        editable_until=datetime.utcnow()
        + timedelta(minutes=settings.edit_window_minutes),
    )
    session.add(log)

    # -- update aggregate user stats --------------------------------------
    _update_streak(user, today)
    user.total_study_hours = round(user.total_study_hours + hours, 3)
    user.xp += xp_earned
    from utils.helpers import level_from_xp

    user.level = level_from_xp(user.xp)
    user.longest_streak = max(user.longest_streak, user.current_streak)

    # -- daily goal progress ----------------------------------------------
    goal_completed = await _touch_daily_goal(session, user, today, daily_total + hours)

    await session.flush()
    leveled_up = user.level > old_level
    logger.info(
        "Logged %.2fh of %s for user %s (xp=%d, level=%d->%d)",
        hours,
        subject,
        telegram_id,
        xp_earned,
        old_level,
        user.level,
    )
    return LogResult(
        log=log,
        xp_earned=xp_earned,
        leveled_up=leveled_up,
        new_level=user.level,
        goal_completed=goal_completed,
    )


def _update_streak(user: User, today) -> None:
    """Advance or reset a user's current streak based on the last study date."""
    if user.last_study_date is None:
        user.current_streak = 1
    elif user.last_study_date == today:
        # Already counted today; keep streak as-is.
        return
    elif user.last_study_date == today - timedelta(days=1):
        user.current_streak += 1
    else:
        # Gap larger than one day resets the streak.
        user.current_streak = 1
    user.last_study_date = today


async def _touch_daily_goal(
    session: AsyncSession, user: User, day, new_total: float
) -> bool:
    """Mark today's goal complete if the running total meets it."""
    result = await session.execute(
        select(DailyGoal).where(
            DailyGoal.telegram_id == user.telegram_id,
            DailyGoal.goal_date == day,
        )
    )
    goal = result.scalar_one_or_none()
    if goal is None or goal.completed:
        return False
    if new_total >= goal.goal_hours > 0:
        goal.completed = True
        user.goals_completed += 1
        return True
    return False


# ---------------------------------------------------------------------------
# Edit / undo
# ---------------------------------------------------------------------------
async def _latest_editable_log(
    session: AsyncSession, telegram_id: int
) -> Optional[StudyLog]:
    result = await session.execute(
        select(StudyLog)
        .where(StudyLog.telegram_id == telegram_id)
        .order_by(StudyLog.timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def undo_last(session: AsyncSession, telegram_id: int) -> Optional[Tuple[StudyLog, User]]:
    """Undo the most recent log if it is still within the edit window."""
    log = await _latest_editable_log(session, telegram_id)
    user = await session.get(User, (await _user_pk(session, telegram_id)))
    if log is None or user is None:
        return None
    if log.editable_until is None or log.editable_until < datetime.utcnow():
        raise StudyServiceError("The edit window (10 minutes) has expired.")
    return await _revert_log(session, user, log)


async def edit_last(
    session: AsyncSession,
    telegram_id: int,
    *,
    hours: Optional[float] = None,
    subject: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[Tuple[StudyLog, User]]:
    """Edit the most recent log's fields (within the edit window)."""
    log = await _latest_editable_log(session, telegram_id)
    if log is None:
        return None
    if log.editable_until is None or log.editable_until < datetime.utcnow():
        raise StudyServiceError("The edit window (10 minutes) has expired.")

    user_pk = await _user_pk(session, telegram_id)
    user = await session.get(User, user_pk)
    if user is None:
        return None

    delta = 0.0
    if hours is not None:
        delta = hours - log.hours
        log.hours = hours
    if subject is not None:
        log.subject = subject
    if note is not None:
        log.note = note or None
    if delta:
        # keep user totals in sync without re-rolling streaks/xp milestones
        user.total_study_hours = round(user.total_study_hours + delta, 3)
        user.xp = max(0, user.xp + xp_for_hours(delta))
        from utils.helpers import level_from_xp

        user.level = level_from_xp(user.xp)
    await session.flush()
    return log, user


async def _revert_log(
    session: AsyncSession, user: User, log: StudyLog
) -> Tuple[StudyLog, User]:
    """Reverse the aggregate effects of a log before deleting it."""
    user.total_study_hours = round(max(0.0, user.total_study_hours - log.hours), 3)
    user.xp = max(0, user.xp - log.xp_earned)
    from utils.helpers import level_from_xp

    user.level = level_from_xp(user.xp)
    await session.delete(log)
    await session.flush()
    return log, user


async def _user_pk(session: AsyncSession, telegram_id: int) -> int:
    """Resolve the surrogate primary key for a Telegram id."""
    from services.user_service import get_user

    user = await get_user(session, telegram_id)
    if user is None:
        raise StudyServiceError("User not found.")
    return user.id


# ---------------------------------------------------------------------------
# Misc queries for reports
# ---------------------------------------------------------------------------
async def logs_for_day(session: AsyncSession, day) -> List[StudyLog]:
    result = await session.execute(
        select(StudyLog).where(StudyLog.log_date == day)
    )
    return list(result.scalars().all())


async def active_telegram_ids_today(session: AsyncSession) -> set:
    result = await session.execute(
        select(StudyLog.telegram_id).where(StudyLog.log_date == today_utc())
    )
    return {row[0] for row in result.all()}


async def users_without_log_since(session: AsyncSession, since) -> List[User]:
    """Users with no study log at or after *since*."""
    from models import User

    subq = (
        select(StudyLog.telegram_id)
        .where(StudyLog.log_date >= since)
        .distinct()
        .subquery()
    )
    result = await session.execute(
        select(User)
        .where(User.is_active.is_(True))
        .where(User.telegram_id.notin_(select(subq.c.telegram_id)))
    )
    return list(result.scalars().all())
