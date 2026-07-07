"""Leaderboard computation with rank-movement tracking and caching."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Setting, StudyLog, User
from services.gamification_service import badge_summary, badges_list
from services.user_service import display_name_of
from utils.time_utils import last_n_days, start_of_week, today_utc

logger = logging.getLogger(__name__)

#: How long (seconds) a leaderboard stays cached in memory.
CACHE_TTL = 60.0

SCOPE_DAYS = {"daily": 1, "weekly": 7, "monthly": 30, "alltime": None}
VALID_SCOPES = set(SCOPE_DAYS)


@dataclass
class LeaderboardEntry:
    telegram_id: int
    name: str
    hours: float
    xp: int
    level: int
    badges: str
    rank: int
    movement: str = "same"  # up | down | same | new


@dataclass
class Leaderboard:
    scope: str
    entries: List[LeaderboardEntry] = field(default_factory=list)
    generated_at: float = 0.0


# In-process cache keyed by scope.
_cache: Dict[str, tuple] = {}


async def _rank_snapshot(session: AsyncSession, scope: str) -> Dict[int, int]:
    """Read the previously stored ranks for a scope from the settings table."""
    result = await session.execute(
        select(Setting).where(Setting.key == f"lb_ranks_{scope}")
    )
    setting = result.scalar_one_or_none()
    if not setting or not setting.value:
        return {}
    try:
        raw = json.loads(setting.value)
        return {int(k): int(v) for k, v in raw.items()}
    except (ValueError, TypeError):
        return {}


async def _save_rank_snapshot(
    session: AsyncSession, scope: str, ranks: Dict[int, int]
) -> None:
    result = await session.execute(
        select(Setting).where(Setting.key == f"lb_ranks_{scope}")
    )
    setting = result.scalar_one_or_none()
    payload = json.dumps({str(k): v for k, v in ranks.items()})
    if setting is None:
        session.add(Setting(key=f"lb_ranks_{scope}", value=payload))
    else:
        setting.value = payload
        setting.updated_at = func.now()


async def _hours_by_scope(
    session: AsyncSession, scope: str
) -> Dict[int, float]:
    """Return ``{telegram_id: hours}`` for the requested scope."""
    days = SCOPE_DAYS[scope]
    if scope == "alltime":
        stmt = (
            select(StudyLog.telegram_id, func.sum(StudyLog.hours))
            .group_by(StudyLog.telegram_id)
        )
    else:
        window = last_n_days(today_utc(), days)
        stmt = (
            select(StudyLog.telegram_id, func.sum(StudyLog.hours))
            .where(StudyLog.log_date >= window[0])
            .group_by(StudyLog.telegram_id)
        )
    result = await session.execute(stmt)
    return {telegram_id: float(h) for telegram_id, h in result.all()}


async def get_leaderboard(
    session: AsyncSession, scope: str = "weekly", use_cache: bool = True
) -> Leaderboard:
    """Build (or fetch a cached copy of) a leaderboard.

    ``scope`` is one of daily / weekly / monthly / alltime.
    """
    if scope not in VALID_SCOPES:
        scope = "weekly"

    cached = _cache.get(scope)
    if use_cache and cached and (time.monotonic() - cached[0]) < CACHE_TTL:
        return cached[1]

    hours_map = await _hours_by_scope(session, scope)
    if not hours_map:
        board = Leaderboard(scope=scope, generated_at=time.time())
        _cache[scope] = (time.monotonic(), board)
        return board

    users_result = await session.execute(
        select(User).where(User.telegram_id.in_(hours_map.keys()))
    )
    users = {u.telegram_id: u for u in users_result.scalars().all()}

    ranked = sorted(hours_map.items(), key=lambda kv: kv[1], reverse=True)
    previous = await _rank_snapshot(session, scope)

    entries: List[LeaderboardEntry] = []
    new_snapshot: Dict[int, int] = {}
    for index, (telegram_id, hours) in enumerate(ranked, start=1):
        user = users.get(telegram_id)
        prev_rank = previous.get(telegram_id)
        if prev_rank is None:
            movement = "new" if telegram_id not in previous else "same"
        elif prev_rank > index:
            movement = "up"
        elif prev_rank < index:
            movement = "down"
        else:
            movement = "same"
        entries.append(
            LeaderboardEntry(
                telegram_id=telegram_id,
                name=display_name_of(user),
                hours=round(hours, 2),
                xp=user.xp if user else 0,
                level=user.level if user else 0,
                badges=badge_summary(user) if user else "—",
                rank=index,
                movement=movement,
            )
        )
        new_snapshot[telegram_id] = index

    await _save_rank_snapshot(session, scope, new_snapshot)
    board = Leaderboard(scope=scope, entries=entries, generated_at=time.time())
    _cache[scope] = (time.monotonic(), board)
    return board


def clear_cache(scope: Optional[str] = None) -> None:
    """Invalidate cached leaderboards (call after a new log)."""
    if scope is None:
        _cache.clear()
    else:
        _cache.pop(scope, None)


async def rank_of(session: AsyncSession, telegram_id: int, scope: str = "weekly") -> Optional[LeaderboardEntry]:
    board = await get_leaderboard(session, scope)
    for entry in board.entries:
        if entry.telegram_id == telegram_id:
            return entry
    return None
