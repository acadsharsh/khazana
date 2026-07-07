"""Integration tests for handlers using lightweight Telegram fakes."""
import pytest

import config
from handlers import admin, analytics, gamification, study_log
from services import study_service, user_service
from tests.fakes import FakeUpdate


async def _run(handler, args=None, user_id=1, name="Alice"):
    update = FakeUpdate(args=args)
    update.effective_user.id = user_id
    update.effective_user.username = name.lower()
    update.effective_user.full_name = name
    context = update.context
    await handler(update, context)
    return update


async def test_cmd_log_success(session_factory):
    update = await _run(study_log.cmd_log, args=["Math", "2"])
    body = update.effective_message.replies[-1]["text"]
    assert "Logged" in body
    assert "Math" in body
    # persisted
    async with session_factory() as s:
        user = await user_service.get_user(s, 1)
        assert user.total_study_hours == 2.0


async def test_cmd_log_invalid_hours(session_factory):
    update = await _run(study_log.cmd_log, args=["0"])
    assert "❌" in update.effective_message.replies[-1]["text"]


async def test_cmd_log_bad_usage(session_factory):
    update = await _run(study_log.cmd_log, args=[])
    assert "Usage" in update.effective_message.replies[-1]["text"]


async def test_cmd_goal_and_progress(session_factory):
    await _run(study_log.cmd_goal, args=["5"])
    update = await _run(study_log.cmd_progress)
    body = update.effective_message.replies[-1]["text"]
    assert "progress" in body.lower() or "5h" in body


async def test_cmd_undo(session_factory):
    await _run(study_log.cmd_log, args=["Math", "2"])
    update = await _run(study_log.cmd_undo)
    assert "Undid" in update.effective_message.replies[-1]["text"]
    async with session_factory() as s:
        user = await user_service.get_user(s, 1)
        assert user.total_study_hours == 0.0


async def test_cmd_stats(session_factory):
    await _run(study_log.cmd_log, args=["Math", "3"])
    update = await _run(gamification.cmd_stats)
    body = update.effective_message.replies[-1]["text"]
    assert "Statistics" in body
    assert "Lifetime" in body


async def test_cmd_streak(session_factory):
    await _run(study_log.cmd_log, args=["Math", "2"])
    update = await _run(gamification.cmd_streak)
    assert "Streak" in update.effective_message.replies[-1]["text"]


async def test_cmd_leaderboard(session_factory):
    await _run(study_log.cmd_log, args=["Math", "2"], user_id=1, name="Alice")
    await _run(study_log.cmd_log, args=["Math", "5"], user_id=2, name="Bob")
    update = await _run(gamification.cmd_leaderboard, args=["daily"])
    body = update.effective_message.replies[-1]["text"]
    assert "Leaderboard" in body
    assert "Bob" in body


async def test_cmd_rank(session_factory):
    await _run(study_log.cmd_log, args=["Math", "2"])
    update = await _run(gamification.cmd_rank)
    assert "rank" in update.effective_message.replies[-1]["text"].lower()


async def test_cmd_focuscheck(session_factory):
    await _run(study_log.cmd_log, args=["Math", "3"])
    update = await _run(analytics.cmd_focuscheck)
    body = update.effective_message.replies[-1]["text"]
    assert "Focus Check" in body


async def test_cmd_subjects(session_factory):
    await _run(study_log.cmd_log, args=["Math", "3"])
    update = await _run(analytics.cmd_subjects)
    assert "Subjects" in update.effective_message.replies[-1]["text"]


async def test_admin_rejected_for_non_admin(session_factory):
    update = await _run(admin.cmd_adminstats)
    body = update.effective_message.replies[-1]["text"]
    assert "restricted" in body.lower()


async def test_admin_allowed_when_in_admin_ids(session_factory):
    admin_id = 999999
    config.settings.admin_ids.append(admin_id)
    try:
        update = await _run(admin.cmd_adminstats, user_id=admin_id)
        body = update.effective_message.replies[-1]["text"]
        assert "Admin Dashboard" in body
    finally:
        config.settings.admin_ids.remove(admin_id)


async def test_admin_export_csv(session_factory):
    admin_id = 999999
    config.settings.admin_ids.append(admin_id)
    try:
        await _run(study_log.cmd_log, args=["Math", "2"])
        update = await _run(admin.cmd_export, args=["csv"], user_id=admin_id)
        assert update.effective_message.documents  # a document was sent
    finally:
        config.settings.admin_ids.remove(admin_id)
