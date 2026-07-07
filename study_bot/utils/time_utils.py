"""Time & timezone helpers.

All "now" calculations use UTC internally; a user's configured timezone is only
used for display purposes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo


def get_zoneinfo(name: str):
    """Return a :class:`ZoneInfo` for *name*, falling back to UTC."""
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def now_utc() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def today_utc() -> date:
    """Return today's date in UTC."""
    return now_utc().date()


def to_utc_naive(dt: datetime) -> datetime:
    """Convert a (possibly aware) datetime to a naive UTC datetime."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def week_number(d: date) -> int:
    """ISO week number (1-53) for a date."""
    return d.isocalendar()[1]


def month_of(d: date) -> int:
    return d.month


def year_of(d: date) -> int:
    return d.year


def start_of_week(d: date) -> date:
    """Return the Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


def date_range(start: date, end: date) -> List[date]:
    """Inclusive list of dates from *start* to *end*."""
    out: List[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def last_n_days(reference: Optional[date] = None, n: int = 7) -> List[date]:
    """Return the last *n* days ending today (inclusive)."""
    reference = reference or today_utc()
    return date_range(reference - timedelta(days=n - 1), reference)


def days_ago(n: int) -> date:
    return today_utc() - timedelta(days=n)


def format_dt(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a datetime for display, returning '-' for ``None``."""
    if not dt:
        return "-"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime(fmt)


def parse_time_pair(value: str) -> tuple[int, int]:
    """Parse a ``HH:MM`` string into a (hour, minute) tuple."""
    hour, minute = value.split(":")
    return int(hour), int(minute)
