"""Pure helper functions: gamification math, scoring and decorators.

These functions have no database dependency which keeps them trivially unit
testable.
"""
from __future__ import annotations

import functools
import math
import time
from typing import Awaitable, Callable, Dict, List, Tuple

from config import settings


# ---------------------------------------------------------------------------
# Gamification math
# ---------------------------------------------------------------------------
def xp_for_hours(hours: float, xp_per_hour: int = settings.xp_per_hour) -> int:
    """XP earned for a number of studied hours."""
    return int(hours * xp_per_hour)


def level_from_xp(total_xp: int) -> int:
    """Level = floor(sqrt(total_xp / 100))."""
    if total_xp <= 0:
        return 0
    return int(math.sqrt(total_xp / 100.0))


def xp_for_next_level(total_xp: int) -> Tuple[int, float]:
    """Return (next_level_threshold_xp, progress_fraction 0..1)."""
    current_level = level_from_xp(total_xp)
    current_floor_xp = current_level * current_level * 100
    next_level = current_level + 1
    next_floor_xp = next_level * next_level * 100
    span = next_floor_xp - current_floor_xp
    progress = 0.0 if span <= 0 else (total_xp - current_floor_xp) / span
    return next_floor_xp, max(0.0, min(progress, 1.0))


def compute_efficiency(
    total_hours: float,
    study_days_30: int,
    current_streak: int,
    goals_completed: int,
    goals_set: int,
) -> float:
    """Efficiency score out of 100.

    Weighting:

    * 40% - total study hours (normalised against 50h)
    * 30% - consistency (distinct study days / 30)
    * 20% - current streak (normalised against 30 days)
    * 10% - goal completion rate
    """
    hours_score = min(total_hours / 50.0, 1.0) * 40
    consistency = min(study_days_30 / 30.0, 1.0) * 30
    streak_score = min(current_streak / 30.0, 1.0) * 20
    goal_rate = (goals_completed / goals_set) if goals_set > 0 else 0.0
    goal_score = min(goal_rate, 1.0) * 10
    return round(hours_score + consistency + streak_score + goal_score, 1)


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """A minimal sliding-window rate limiter keyed by an arbitrary id."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: Dict[str, List[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits.setdefault(key, [])
        cutoff = now - self.window
        # drop old entries
        bucket[:] = [ts for ts in bucket if ts > cutoff]
        if len(bucket) >= self.max_calls:
            return False
        bucket.append(now)
        return True


#: Shared limiter used by the rate-limit decorator (10 commands / 15s / user).
default_limiter = RateLimiter(max_calls=12, window_seconds=15.0)


def rate_limited(limiter: RateLimiter = default_limiter):
    """Decorator that throttles commands per Telegram user."""

    def decorator(func: Callable[..., Awaitable]):
        @functools.wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            user = update.effective_user
            if user is not None and not limiter.allow(str(user.id)):
                if update.effective_message is not None:
                    await update.effective_message.reply_text(
                        "⏳ Slow down a little - you're sending commands too fast.",
                    )
                return None
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


def admin_only(func: Callable[..., Awaitable]):
    """Restrict a handler to users listed in ``ADMIN_IDS``."""

    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user = update.effective_user
        if user is None or not settings.is_admin(user.id):
            if update.effective_message is not None:
                await update.effective_message.reply_text(
                    "🚫 This command is restricted to administrators."
                )
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


def private_or_group_text(update) -> str:
    """Best-effort display name for the sender of an update."""
    user = update.effective_user
    if not user:
        return "Unknown"
    return user.full_name or user.username or str(user.id)
