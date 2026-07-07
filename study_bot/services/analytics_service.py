"""Analytics: statistics, subject breakdowns, study calendar, efficiency & focus.

Heavy aggregation queries live here so handlers stay thin.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DailyGoal, StudyLog, User
from services import gamification_service, study_service
from services.user_service import display_name_of
from utils.helpers import compute_efficiency, level_from_xp
from utils.time_utils import (
    date_range,
    days_ago,
    last_n_days,
    start_of_week,
    today_utc,
)


# ---------------------------------------------------------------------------
# Per-user statistics
# ---------------------------------------------------------------------------
async def user_stats(session: AsyncSession, user: User) -> Dict:
    """Compile the dictionary rendered by /stats."""
    telegram_id = user.telegram_id
    today = today_utc()

    today_h = await study_service.hours_today(session, telegram_id)
    week_h = await study_service.hours_this_week(session, telegram_id)
    month_h = await study_service.hours_this_month(session, telegram_id)
    lifetime = user.total_study_hours
    study_days = await study_service.distinct_study_days(session, telegram_id, 30)
    avg = lifetime / study_days if study_days else 0.0

    subjects = await subject_breakdown(session, telegram_id)
    fav = max(subjects, key=subjects.get) if subjects else "—"
    achievements = await gamification_service.user_achievements(session, telegram_id)

    next_level_xp, level_progress = _next_level(user)

    return {
        "display_name": display_name_of(user),
        "today": today_h,
        "week": week_h,
        "month": month_h,
        "lifetime": lifetime,
        "current_streak": user.current_streak,
        "longest_streak": user.longest_streak,
        "average": round(avg, 2),
        "xp": user.xp,
        "level": user.level,
        "level_progress": level_progress,
        "next_level_xp": next_level_xp,
        "achievement_count": len(achievements),
        "goals_completed": user.goals_completed,
        "favorite_subject": fav,
        "subjects": subjects,
        "study_days_30": study_days,
        "calendar": await study_calendar(session, telegram_id, 30),
        "badges": gamification_service.badge_summary(user),
    }


def _next_level(user: User):
    from utils.helpers import xp_for_next_level

    return xp_for_next_level(user.xp)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
async def subject_breakdown(
    session: AsyncSession, telegram_id: int
) -> Dict[str, float]:
    """Return ``{subject: total_hours}`` for a user (all time)."""
    result = await session.execute(
        select(StudyLog.subject, func.sum(StudyLog.hours))
        .where(StudyLog.telegram_id == telegram_id)
        .group_by(StudyLog.subject)
        .order_by(func.sum(StudyLog.hours).desc())
    )
    return {subject: float(hours) for subject, hours in result.all()}


async def subject_history(
    session: AsyncSession, telegram_id: int, subject: str, days: int = 30
) -> Dict[date, float]:
    window = last_n_days(today_utc(), days)
    result = await session.execute(
        select(StudyLog.log_date, func.sum(StudyLog.hours))
        .where(
            StudyLog.telegram_id == telegram_id,
            StudyLog.subject == subject,
            StudyLog.log_date >= window[0],
        )
        .group_by(StudyLog.log_date)
        .order_by(StudyLog.log_date)
    )
    return {d: float(h) for d, h in result.all()}


# ---------------------------------------------------------------------------
# Calendar / heatmap
# ---------------------------------------------------------------------------
async def study_calendar(
    session: AsyncSession, telegram_id: int, days: int = 30
) -> List[tuple]:
    """Return ``[(date, hours_or_0), ...]`` for the last *days*."""
    window = last_n_days(today_utc(), days)
    result = await session.execute(
        select(StudyLog.log_date, func.sum(StudyLog.hours))
        .where(
            StudyLog.telegram_id == telegram_id,
            StudyLog.log_date >= window[0],
        )
        .group_by(StudyLog.log_date)
    )
    by_date = {d: float(h) for d, h in result.all()}
    return [(d, by_date.get(d, 0.0)) for d in window]


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------
async def efficiency_for(
    session: AsyncSession, user: User
) -> float:
    """Compute a user's efficiency score out of 100."""
    goals_set = await _goal_days(session, user.telegram_id)
    return compute_efficiency(
        total_hours=user.total_study_hours,
        study_days_30=await study_service.distinct_study_days(
            session, user.telegram_id, 30
        ),
        current_streak=user.current_streak,
        goals_completed=user.goals_completed,
        goals_set=max(goals_set, user.goals_completed),
    )


async def _goal_days(session: AsyncSession, telegram_id: int) -> int:
    result = await session.execute(
        select(func.count(DailyGoal.id)).where(
            DailyGoal.telegram_id == telegram_id
        )
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Focus check (group wide)
# ---------------------------------------------------------------------------
async def focus_check(session: AsyncSession) -> Dict:
    """Aggregate numbers behind the /focuscheck command."""
    today = today_utc()
    yesterday = today - timedelta(days=1)

    per_user = {}
    result = await session.execute(
        select(
            StudyLog.telegram_id,
            func.sum(StudyLog.hours),
        )
        .where(StudyLog.log_date == today)
        .group_by(StudyLog.telegram_id)
    )
    for telegram_id, hours in result.all():
        per_user[telegram_id] = float(hours)

    active_users = await session.execute(
        select(User).where(User.is_active.is_(True))
    )
    active_list = list(active_users.scalars().all())
    active_ids = {u.telegram_id for u in active_list}
    studied_today = set(per_user.keys()) & active_ids

    total = sum(per_user.values())
    avg = total / len(active_ids) if active_ids else 0.0
    inactive = [u for u in active_list if u.telegram_id not in studied_today]

    return {
        "today": today,
        "total_hours": round(total, 2),
        "average_hours": round(avg, 2),
        "active_users": len(studied_today),
        "inactive_users": len(inactive),
        "inactive_names": [display_name_of(u) for u in inactive[:10]],
        "total_members": len(active_ids),
    }


# ---------------------------------------------------------------------------
# Group aggregates for reports
# ---------------------------------------------------------------------------
async def group_aggregates(
    session: AsyncSession, start: date, end: date
) -> Dict[int, Dict]:
    """Return ``{telegram_id: {hours, days, ...}}`` for a date range."""
    result = await session.execute(
        select(
            StudyLog.telegram_id,
            func.sum(StudyLog.hours),
            func.count(func.distinct(StudyLog.log_date)),
        )
        .where(StudyLog.log_date >= start, StudyLog.log_date <= end)
        .group_by(StudyLog.telegram_id)
    )
    out: Dict[int, Dict] = {}
    for telegram_id, hours, days in result.all():
        out[telegram_id] = {"hours": float(hours), "days": int(days)}
    return out
