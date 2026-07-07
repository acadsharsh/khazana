"""User registration & lookup service."""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User

logger = logging.getLogger(__name__)


async def get_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Fetch a single user by Telegram id (or ``None``)."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_username(
    session: AsyncSession, username: str
) -> Optional[User]:
    """Case-insensitive lookup by Telegram username."""
    result = await session.execute(
        select(User).where(User.username.ilike(username.lstrip("@")))
    )
    return result.scalar_one_or_none()


async def list_users(session: AsyncSession, active_only: bool = True) -> List[User]:
    """Return every (optionally active) registered user."""
    stmt = select(User)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    stmt = stmt.order_by(User.total_study_hours.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str],
    full_name: Optional[str],
    display_name: Optional[str] = None,
    timezone_name: str = "UTC",
) -> User:
    """Return an existing user or create & persist a new one.

    Existing users have their volatile fields (username / full name) refreshed
    so the data never goes stale.
    """
    user = await get_user(session, telegram_id)
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            display_name=display_name or full_name or username or str(telegram_id),
            timezone=timezone_name,
        )
        session.add(user)
        await session.flush()
        logger.info("Registered new user id=%s username=%s", telegram_id, username)
    else:
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if not user.display_name:
            user.display_name = display_name or full_name or username or str(telegram_id)
            changed = True
        if changed:
            await session.flush()
    return user


async def set_display_name(
    session: AsyncSession, telegram_id: int, display_name: str
) -> Optional[User]:
    user = await get_user(session, telegram_id)
    if user is not None:
        user.display_name = display_name.strip()[:128] or user.display_name
    return user


async def deactivate(session: AsyncSession, telegram_id: int) -> bool:
    user = await get_user(session, telegram_id)
    if user is None:
        return False
    user.is_active = False
    return True


async def reset_progress(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Wipe a user's stats while keeping the account."""
    user = await get_user(session, telegram_id)
    if user is None:
        return None
    user.total_study_hours = 0.0
    user.current_streak = 0
    user.longest_streak = 0
    user.xp = 0
    user.level = 0
    user.badges = "[]"
    user.goals_completed = 0
    user.last_study_date = None
    return user


def display_name_of(user: Optional[User]) -> str:
    if user is None:
        return "Unknown"
    return user.display_name or user.full_name or user.username or str(user.telegram_id)
