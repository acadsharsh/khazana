"""Gamification: achievements catalog, badge awarding and persistence."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Achievement, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AchievementDef:
    """Static definition of an awardable achievement."""

    code: str
    name: str
    description: str
    emoji: str
    kind: str  # "hours" | "streak" | "special"
    threshold: float


#: The complete achievement catalogue (requirements #10).
ACHIEVEMENTS: List[AchievementDef] = [
    AchievementDef("first_log", "First Steps", "Log your first study session", "🐣", "special", 1),
    AchievementDef("hours_10", "Getting Started", "Reach 10 study hours", "📗", "hours", 10),
    AchievementDef("hours_50", "Scholar", "Reach 50 study hours", "📘", "hours", 50),
    AchievementDef("hours_100", "Centurion", "Reach 100 study hours", "📕", "hours", 100),
    AchievementDef("hours_250", "Knowledge Seeker", "Reach 250 study hours", "🏅", "hours", 250),
    AchievementDef("hours_500", "Mastermind", "Reach 500 study hours", "🥇", "hours", 500),
    AchievementDef("hours_1000", "Legend", "Reach 1000 study hours", "👑", "hours", 1000),
    AchievementDef("streak_7", "On Fire", "Maintain a 7-day streak", "🔥", "streak", 7),
    AchievementDef("streak_30", "Unstoppable", "Maintain a 30-day streak", "⚡", "streak", 30),
    AchievementDef("streak_100", "Iron Will", "Maintain a 100-day streak", "💎", "streak", 100),
]

ACHIEVEMENT_BY_CODE: Dict[str, AchievementDef] = {a.code: a for a in ACHIEVEMENTS}


def badges_list(user: User) -> List[str]:
    """Decode a user's stored badge codes."""
    try:
        return list(json.loads(user.badges or "[]"))
    except (ValueError, TypeError):
        return []


def badge_summary(user: User) -> str:
    """Render a user's earned badges as a compact emoji string."""
    codes = badges_list(user)
    emojis = [ACHIEVEMENT_BY_CODE[c].emoji for c in codes if c in ACHIEVEMENT_BY_CODE]
    return " ".join(emojis) if emojis else "—"


async def _already_awarded(
    session: AsyncSession, telegram_id: int, code: str
) -> bool:
    result = await session.execute(
        select(Achievement.id).where(
            Achievement.telegram_id == telegram_id,
            Achievement.code == code,
        )
    )
    return result.first() is not None


def _qualifies(user: User, definition: AchievementDef) -> bool:
    if definition.kind == "hours":
        return user.total_study_hours >= definition.threshold
    if definition.kind == "streak":
        return user.longest_streak >= definition.threshold
    if definition.code == "first_log":
        return user.total_study_hours > 0
    return False


async def evaluate_user(
    session: AsyncSession, user: User
) -> List[AchievementDef]:
    """Award every achievement the user now qualifies for but lacks.

    Also keeps ``user.badges`` in sync.  Returns the freshly awarded defs so
    handlers can announce them.
    """
    newly: List[AchievementDef] = []
    current_badges = set(badges_list(user))

    for definition in ACHIEVEMENTS:
        if definition.code in current_badges:
            continue
        if not _qualifies(user, definition):
            continue
        if await _already_awarded(session, user.telegram_id, definition.code):
            current_badges.add(definition.code)
            continue

        session.add(
            Achievement(
                telegram_id=user.telegram_id,
                code=definition.code,
                name=definition.name,
                description=definition.description,
            )
        )
        current_badges.add(definition.code)
        newly.append(definition)
        logger.info(
            "Achievement %s awarded to user %s", definition.code, user.telegram_id
        )

    user.badges = json.dumps(sorted(current_badges))
    if newly:
        await session.flush()
    return newly


async def user_achievements(
    session: AsyncSession, telegram_id: int
) -> List[Achievement]:
    result = await session.execute(
        select(Achievement)
        .where(Achievement.telegram_id == telegram_id)
        .order_by(Achievement.achieved_at.desc())
    )
    return list(result.scalars().all())


def all_definitions() -> List[AchievementDef]:
    return list(ACHIEVEMENTS)


def progress_to_next_hours_badge(user: User) -> Optional[Tuple[AchievementDef, float]]:
    """Return the next unearned hours badge and completion ratio (0..1)."""
    earned = set(badges_list(user))
    next_badge: Optional[AchievementDef] = None
    for definition in ACHIEVEMENTS:
        if definition.kind != "hours":
            continue
        if definition.code in earned:
            continue
        next_badge = definition
        break
    if next_badge is None:
        return None
    ratio = min(user.total_study_hours / next_badge.threshold, 1.0) if next_badge.threshold else 0.0
    return next_badge, ratio
