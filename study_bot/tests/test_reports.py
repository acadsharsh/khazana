"""Tests for report generation."""
import pytest

from reports.report_builder import (
    build_daily_report,
    build_monthly_report,
    build_weekly_report,
)
from services import study_service, user_service


async def _seed(session):
    alice = await user_service.get_or_create_user(session, 1, "alice", "Alice")
    bob = await user_service.get_or_create_user(session, 2, "bob", "Bob")
    await study_service.log_study(session, alice, "Math", 3, "")
    await study_service.log_study(session, bob, "Physics", 1.5, "")
    await session.commit()
    return alice, bob


async def test_daily_report(session):
    await _seed(session)
    text = await build_daily_report(session)
    assert "Daily Study Report" in text
    assert "Alice" in text
    assert "Bob" in text
    assert "Missing Task List" in text or "Everyone logged" in text


async def test_weekly_report(session):
    await _seed(session)
    text = await build_weekly_report(session)
    assert "Weekly Report" in text
    assert "Top Performer" in text
    assert "Subject Distribution" in text


async def test_monthly_report(session):
    await _seed(session)
    text = await build_monthly_report(session)
    assert "Monthly Report" in text
    assert "Monthly Leaderboard" in text
    assert "Total Hours" in text


async def test_empty_reports(session):
    daily = await build_daily_report(session)
    weekly = await build_weekly_report(session)
    monthly = await build_monthly_report(session)
    assert isinstance(daily, str)
    assert "No study time" in weekly
    assert "No study time" in monthly
