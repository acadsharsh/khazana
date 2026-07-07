"""Unit tests for pure utility functions (no database)."""
import pytest

from utils.formatting import format_table, progress_bar, escape_html, human_hours
from utils.helpers import (
    RateLimiter,
    compute_efficiency,
    level_from_xp,
    xp_for_hours,
    xp_for_next_level,
)
from utils.time_utils import last_n_days, start_of_week, week_number
from utils.validation import (
    ValidationError,
    parse_log_command,
    parse_username,
    sanitise_subject,
    validate_goal_hours,
    validate_hours,
)


# ----------------------------- validation ----------------------------------
@pytest.mark.parametrize(
    "args, subject, hours, note",
    [
        (["3"], "General", 3.0, ""),
        (["Math", "2"], "Math", 2.0, ""),
        (["Physics", "1.5"], "Physics", 1.5, ""),
        (["DSA", "4"], "Dsa", 4.0, ""),  # subject is title-cased
        (["Math", "3", "Finished", "Dynamic", "Programming"], "Math", 3.0, "Finished Dynamic Programming"),
        (["2", "dynamic", "programming"], "General", 2.0, "dynamic programming"),
    ],
)
def test_parse_log_command(args, subject, hours, note):
    parsed = parse_log_command(args)
    assert parsed.subject == subject
    assert parsed.hours == hours
    assert parsed.note == note


def test_parse_log_command_errors():
    with pytest.raises(ValidationError):
        parse_log_command([])
    with pytest.raises(ValidationError):
        parse_log_command(["Math"])  # subject but no hours
    with pytest.raises(ValidationError):
        parse_log_command(["Math", "lots"])  # non-numeric hours


def test_validate_hours():
    with pytest.raises(ValidationError):
        validate_hours(0)
    with pytest.raises(ValidationError):
        validate_hours(-2)
    with pytest.raises(ValidationError):
        validate_hours(20)  # > 16 default cap
    with pytest.raises(ValidationError):
        validate_hours(2, daily_total=15)  # would exceed 16
    validate_hours(5)  # ok


def test_validate_goal_hours():
    validate_goal_hours(4)
    with pytest.raises(ValidationError):
        validate_goal_hours(0)
    with pytest.raises(ValidationError):
        validate_goal_hours(25)


def test_parse_username():
    assert parse_username("@bob_123") == "bob_123"
    assert parse_username("alice") == "alice"
    assert parse_username("!!") is None


def test_sanitise_subject():
    assert sanitise_subject("  math  ") == "Math"
    assert sanitise_subject("") == "General"


# ----------------------------- formatting ----------------------------------
def test_progress_bar():
    assert progress_bar(0, 10) == "░" * 10
    assert progress_bar(5, 10) == "█████" + "░" * 5
    assert progress_bar(20, 10) == "█" * 10  # capped
    assert progress_bar(1, 0) == "░" * 10  # zero goal safe


def test_format_table():
    out = format_table(["A", "B"], [["1", "2"], ["30", "400"]])
    assert out.startswith("<pre>")
    assert "A" in out and "B" in out
    assert "30" in out and "400" in out


def test_escape_html_and_human_hours():
    assert escape_html("<b>") == "&lt;b&gt;"
    assert human_hours(3) == "3h"
    assert human_hours(1.5) == "1.5h"


# ----------------------------- gamification math ---------------------------
def test_xp_and_levels():
    assert xp_for_hours(3) == 30
    assert level_from_xp(0) == 0
    assert level_from_xp(100) == 1
    assert level_from_xp(900) == 3  # sqrt(9)
    next_xp, frac = xp_for_next_level(100)
    assert next_xp == 400  # level 2 floor
    assert 0 <= frac <= 1


def test_compute_efficiency_bounds():
    score = compute_efficiency(
        total_hours=60, study_days_30=30, current_streak=30,
        goals_completed=10, goals_set=10,
    )
    assert 0 <= score <= 100
    assert score == 100.0  # everything maxed
    low = compute_efficiency(0, 0, 0, 0, 0)
    assert low == 0.0


# ----------------------------- time helpers --------------------------------
def test_time_helpers():
    days = last_n_days(n=7)
    assert len(days) == 7
    assert week_number(days[-1]) == days[-1].isocalendar()[1]
    monday = start_of_week(days[-1])
    assert monday.weekday() == 0


# ----------------------------- rate limiter --------------------------------
def test_rate_limiter():
    limiter = RateLimiter(max_calls=2, window_seconds=10)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False  # over the limit
    assert limiter.allow("other") is True  # independent key
