"""Tests for the service layer (study logging, goals, gamification, etc.)."""
from datetime import date, timedelta

import pytest

from models import DailyGoal, User
from services import (
    analytics_service,
    gamification_service,
    goal_service,
    leaderboard_service,
    reminder_service,
    study_service,
    user_service,
)
from services.study_service import StudyServiceError
from utils.time_utils import today_utc


async def _user(session, telegram_id=1, name="Alice", hours=0.0):
    user = await user_service.get_or_create_user(
        session, telegram_id, name.lower(), name
    )
    if hours:
        await study_service.log_study(session, user, "Math", hours, "")
    await session.commit()
    return user


# ----------------------------- user service --------------------------------
async def test_get_or_create_is_idempotent(session):
    u1 = await user_service.get_or_create_user(session, 1, "alice", "Alice")
    u2 = await user_service.get_or_create_user(session, 1, "alice2", "Alice2")
    assert u1.id == u2.id
    assert u2.username == "alice2"  # volatile field refreshed
    await session.commit()


# ----------------------------- study service -------------------------------
async def test_log_study_updates_stats(session):
    user = await _user(session, 1)
    result = await study_service.log_study(session, user, "Math", 3, "notes")
    await session.commit()
    assert result.xp_earned == 30
    assert user.total_study_hours == 3.0
    assert user.xp == 30
    assert user.current_streak == 1
    assert user.last_study_date == today_utc()
    assert result.log.subject == "Math"


async def test_log_study_rejects_duplicate(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 2, "")
    with pytest.raises(StudyServiceError):
        await study_service.log_study(session, user, "Math", 2, "")
    await session.commit()


async def test_log_study_daily_cap(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 10, "")
    with pytest.raises(StudyServiceError):
        await study_service.log_study(session, user, "Math", 10, "")  # 20 > 16
    await session.commit()


async def test_undo_last(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 4, "")
    await session.commit()
    result = await study_service.undo_last(session, user.telegram_id)
    await session.commit()
    assert result is not None
    assert user.total_study_hours == 0.0


async def test_edit_last(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 4, "")
    await session.commit()
    log, _ = await study_service.edit_last(session, user.telegram_id, hours=6)
    await session.commit()
    assert log.hours == 6
    assert user.total_study_hours == 6.0


def test_streak_update_logic():
    today = today_utc()
    user = User(telegram_id=1)
    study_service._update_streak(user, today)
    assert user.current_streak == 1
    # consecutive day
    user.last_study_date = today - timedelta(days=1)
    study_service._update_streak(user, today)
    assert user.current_streak == 2
    # gap resets
    user.last_study_date = today - timedelta(days=4)
    study_service._update_streak(user, today)
    assert user.current_streak == 1


# ----------------------------- goal service --------------------------------
async def test_set_goal_and_progress(session):
    user = await _user(session, 1)
    await goal_service.set_goal(session, user.telegram_id, 5)
    await session.commit()
    progress = await goal_service.progress_for(session, user.telegram_id)
    assert progress.goal_hours == 5
    assert progress.ratio == 0.0

    # log 5h -> goal completed
    await study_service.log_study(session, user, "Math", 5, "")
    await session.commit()
    progress = await goal_service.progress_for(session, user.telegram_id)
    assert progress.completed is True


# ----------------------------- gamification --------------------------------
async def test_achievements_awarded(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 10, "")  # 10h
    awarded = await gamification_service.evaluate_user(session, user)
    await session.commit()
    codes = {a.code for a in awarded}
    assert "first_log" in codes
    assert "hours_10" in codes
    assert user.level >= 1


async def test_streak_achievement(session):
    user = await _user(session, 1)
    user.longest_streak = 7
    awarded = await gamification_service.evaluate_user(session, user)
    await session.commit()
    assert any(a.code == "streak_7" for a in awarded)


# ----------------------------- leaderboard ---------------------------------
async def test_leaderboard_sorted_and_movement(session):
    a = await _user(session, 1, "Alice")
    await study_service.log_study(session, a, "Math", 2, "")
    b = await _user(session, 2, "Bob")
    await study_service.log_study(session, b, "Math", 5, "")
    await session.commit()

    board = await leaderboard_service.get_leaderboard(session, "daily", use_cache=False)
    assert board.entries[0].name == "Bob"
    assert board.entries[0].hours == 5
    assert board.entries[1].name == "Alice"

    # swap the order and rebuild -> movement should reflect the change
    a2 = await user_service.get_user(session, 1)
    await study_service.log_study(session, a2, "Math", 10, "")
    await session.commit()
    board2 = await leaderboard_service.get_leaderboard(session, "daily", use_cache=False)
    assert board2.entries[0].name == "Alice"
    assert board2.entries[0].movement == "up"


async def test_rank_of(session):
    a = await _user(session, 1, "Alice")
    await study_service.log_study(session, a, "Math", 3, "")
    await session.commit()
    entry = await leaderboard_service.rank_of(session, 1, "daily")
    assert entry is not None
    assert entry.rank == 1


# ----------------------------- analytics -----------------------------------
async def test_subject_breakdown_and_efficiency(session):
    user = await _user(session, 1)
    await study_service.log_study(session, user, "Math", 4, "")
    await study_service.log_study(session, user, "Physics", 6, "")
    await session.commit()
    breakdown = await analytics_service.subject_breakdown(session, user.telegram_id)
    assert breakdown["Math"] == 4
    assert breakdown["Physics"] == 6
    eff = await analytics_service.efficiency_for(session, user)
    assert 0 < eff <= 100


async def test_focus_check(session):
    await _user(session, 1, "Alice")
    await _user(session, 2, "Bob")
    user2 = await user_service.get_user(session, 2)
    await study_service.log_study(session, user2, "Math", 2, "")
    await session.commit()
    data = await analytics_service.focus_check(session)
    assert data["active_users"] == 1
    assert data["inactive_users"] == 1
    assert data["total_hours"] == 2


# ----------------------------- reminders -----------------------------------
async def test_reminders_and_missing(session):
    await _user(session, 1, "Alice")
    await _user(session, 2, "Bob")
    user2 = await user_service.get_user(session, 2)
    await study_service.log_study(session, user2, "Math", 1, "")
    await reminder_service.mark_logged(session, user2.telegram_id)
    await session.commit()

    to_remind = await reminder_service.users_to_remind(session, "morning")
    ids = {u.telegram_id for u in to_remind}
    assert 1 in ids and 2 not in ids
    await session.commit()

    missing = await reminder_service.missing_today(session)
    assert {u.telegram_id for u in missing} == {1}
