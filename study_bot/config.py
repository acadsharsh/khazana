"""Application configuration.

All runtime configuration is loaded from environment variables (optionally
backed by a ``.env`` file) and exposed through a single :data:`settings`
instance.  Using a dataclass keeps the configuration typed, documented and
easy to test.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

try:  # pragma: no cover - dotenv is a soft dependency at import time
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def parse_admin_ids(raw: str) -> List[int]:
    """Parse a comma/semicolon separated list of admin Telegram user ids."""
    if not raw:
        return []
    ids: List[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.append(int(chunk))
    return ids


def _normalize_sqlite_url(url: str) -> str:
    """Make sure a sqlite url uses the async ``aiosqlite`` driver."""
    if url.startswith("sqlite://") and "aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


@dataclass(frozen=True)
class Config:
    """Immutable container for every tunable setting in the bot."""

    bot_token: str
    group_id: str
    database_url: str
    report_time: str
    timezone: str
    admin_ids: List[int]
    focus_threshold: float
    log_level: str
    max_hours_per_day: float
    edit_window_minutes: int
    duplicate_window_minutes: int
    xp_per_hour: int
    reminder_times: List[str]

    # -- convenience accessors -------------------------------------------------
    @property
    def report_hour(self) -> int:
        return int(self.report_time.split(":")[0])

    @property
    def report_minute(self) -> int:
        return int(self.report_time.split(":")[1])

    @property
    def is_configured(self) -> bool:
        """True only when both a token and a group id are present."""
        return bool(self.bot_token) and bool(self.group_id)

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


def load_config() -> Config:
    """Build a :class:`Config` entirely from environment variables."""
    db_url = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///data/study_bot.db"
    db_url = _normalize_sqlite_url(db_url)
    reminder_raw = os.getenv("REMINDER_TIMES", "09:00,14:00,19:00")
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        group_id=os.getenv("GROUP_ID", ""),
        database_url=db_url,
        report_time=os.getenv("REPORT_TIME", "02:30"),
        timezone=os.getenv("TIMEZONE", "UTC"),
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        focus_threshold=float(os.getenv("FOCUS_THRESHOLD", "2.0")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        max_hours_per_day=float(os.getenv("MAX_HOURS_PER_DAY", "16")),
        edit_window_minutes=int(os.getenv("EDIT_WINDOW_MINUTES", "10")),
        duplicate_window_minutes=int(os.getenv("DUPLICATE_WINDOW_MINUTES", "2")),
        xp_per_hour=int(os.getenv("XP_PER_HOUR", "10")),
        reminder_times=[t.strip() for t in reminder_raw.split(",") if t.strip()],
    )


#: Global configuration instance imported across the codebase.
settings: Config = load_config()


# ---------------------------------------------------------------------------
# Runtime overrides (mutable settings that can change while the bot runs,
# e.g. via /setthreshold). Persisted separately so they survive restarts.
# ---------------------------------------------------------------------------
_runtime_overrides: dict = {}


def get_runtime(key: str, default=None):
    """Return a runtime override or *default*."""
    return _runtime_overrides.get(key, default)


def set_runtime(key: str, value) -> None:
    """Set a runtime override."""
    _runtime_overrides[key] = value


def get_focus_threshold() -> float:
    """Effective focus threshold (runtime override or config default)."""
    override = get_runtime("focus_threshold")
    if override is not None:
        try:
            return float(override)
        except (TypeError, ValueError):
            pass
    return settings.focus_threshold
