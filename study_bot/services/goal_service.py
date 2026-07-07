"""Daily goal service: set, query and report progress."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DailyGoal, User
from services import study_service
from utils.time_utils import today_utc


@dataclass
class GoalProgress:
    goal_hours: float
    logged_hours: float
    remaining: float
    completed: bool

    @property
    def ratio(self) -> float:
        if self.goal_hours <= 0:
            return 0.0
        return min(self.logged_hours / self.goal_hours, 1.0)


async def set_goal(
    session: AsyncSession, telegram_id: int, goal_hours: float
) -> DailyGoal:
    """Create or update today's goal for a user."""
    today = today_utc()
    result = await session.execute(
        select(DailyGoal).where(
            DailyGoal.telegram_id == telegram_id,
            DailyGoal.goal_date == today,
        )
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        goal = DailyGoal(
            telegram_id=telegram_id, goal_date=today, goal_hours=goal_hours
        )
        session.add(goal)
    else:
        goal.goal_hours = goal_hours
        # re-evaluate completion against hours already logged today
    await session.flush()

    logged = await study_service.hours_today(session, telegram_id)
    goal.completed = logged >= goal.goal_hours > 0
    return goal


async def today_goal(
    session: AsyncSession, telegram_id: int
) -> Optional[DailyGoal]:
    result = await session.execute(
        select(DailyGoal).where(
            DailyGoal.telegram_id == telegram_id,
            DailyGoal.goal_date == today_utc(),
        )
    )
    return result.scalar_one_or_none()


async def progress_for(
    session: AsyncSession, telegram_id: int
) -> GoalProgress:
    goal = await today_goal(session, telegram_id)
    logged = await study_service.hours_today(session, telegram_id)
    if goal is None:
        return GoalProgress(0.0, logged, 0.0, False)
    remaining = max(0.0, goal.goal_hours - logged)
    return GoalProgress(
        goal_hours=goal.goal_hours,
        logged_hours=logged,
        remaining=remaining,
        completed=goal.completed,
    )
