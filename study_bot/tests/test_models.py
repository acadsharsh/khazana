"""Tests for the ORM models (creation, querying, constraints)."""
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from models import Achievement, DailyGoal, StudyLog, User


async def _make_user(session, telegram_id=100):
    user = User(
        telegram_id=telegram_id,
        username="alice",
        full_name="Alice",
        display_name="Alice",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_create_and_query_user(session):
    user = await _make_user(session, telegram_id=42)
    assert user.id is not None
    assert user.total_study_hours == 0.0
    assert user.current_streak == 0
    assert user.badges == "[]"
    fetched = await session.get(User, user.id)
    assert fetched.telegram_id == 42


async def test_unique_telegram_id(session):
    await _make_user(session, telegram_id=7)
    session.add(User(telegram_id=7, username="dup"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_study_log_relation(session):
    user = await _make_user(session)
    today = date.today()
    log = StudyLog(
        telegram_id=user.telegram_id,
        subject="Math",
        hours=2.0,
        log_date=today,
        week_number=today.isocalendar()[1],
        month=today.month,
        year=today.year,
        timestamp=datetime.utcnow(),
    )
    session.add(log)
    await session.commit()
    # Query logs directly (async sessions don't support lazy relationship loading).
    from sqlalchemy import select

    result = await session.execute(select(StudyLog).where(StudyLog.telegram_id == user.telegram_id))
    logs = list(result.scalars().all())
    assert len(logs) == 1
    assert logs[0].subject == "Math"


async def test_unique_goal_per_day(session):
    user = await _make_user(session)
    today = date.today()
    session.add(DailyGoal(telegram_id=user.telegram_id, goal_date=today, goal_hours=5))
    await session.commit()
    session.add(DailyGoal(telegram_id=user.telegram_id, goal_date=today, goal_hours=6))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_unique_achievement(session):
    user = await _make_user(session)
    session.add(
        Achievement(telegram_id=user.telegram_id, code="first_log", name="First Steps")
    )
    await session.commit()
    session.add(
        Achievement(telegram_id=user.telegram_id, code="first_log", name="dup")
    )
    with pytest.raises(IntegrityError):
        await session.commit()
