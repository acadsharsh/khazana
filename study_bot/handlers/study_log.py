"""Handlers: /log, /editlog, /undo, /goal, /progress."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from telegram import Update
from telegram.ext import ContextTypes

from database import session_scope
from services import gamification_service, goal_service, leaderboard_service, reminder_service, study_service, user_service
from services.study_service import StudyServiceError
from utils.formatting import bold, escape_html, human_hours, progress_bar
from utils.helpers import rate_limited
from utils.validation import (
    ParsedLog,
    ValidationError,
    parse_hours_token,
    parse_log_command,
    sanitise_note,
    sanitise_subject,
    validate_goal_hours,
    validate_hours,
)
from handlers.core import reply_html

logger = logging.getLogger(__name__)


def _log_confirmation(parsed: ParsedLog, result, new_badges) -> str:
    lines = [
        f"✅ Logged <b>{human_hours(parsed.hours)}</b> of "
        f"<b>{escape_html(parsed.subject)}</b>",
        f"⚡ +{result.xp_earned} XP" + (
            f"  🎉 Level up! You reached <b>Level {result.new_level}</b>!"
            if result.leveled_up
            else ""
        ),
    ]
    if result.goal_completed:
        lines.append("🎯 Daily goal completed - great job!")
    if new_badges:
        badges = " ".join(f"{b.emoji} <b>{escape_html(b.name)}</b>" for b in new_badges)
        lines.append(f"🎖️ New achievement: {badges}")
    return "\n".join(lines)


@rate_limited()
async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log a study session: /log [subject] <hours> [note]."""
    try:
        parsed = parse_log_command(context.args)
        validate_hours(parsed.hours)
    except ValidationError as exc:
        await reply_html(update, f"❌ {exc}")
        return

    user = update.effective_user
    try:
        async with session_scope() as session:
            db_user = await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )
            result = await study_service.log_study(
                session, db_user, parsed.subject, parsed.hours, parsed.note
            )
            new_badges = await gamification_service.evaluate_user(session, db_user)
            await reminder_service.mark_logged(session, db_user.telegram_id)
            confirmation = _log_confirmation(parsed, result, new_badges)
            streak = db_user.current_streak
            display_name = db_user.display_name

        leaderboard_service.clear_cache()
        progress = _format_goal_progress(await _goal_progress(db_user.telegram_id))
        await reply_html(update, f"{confirmation}\n🔥 Streak: {streak} days\n{progress}")
    except StudyServiceError as exc:
        await reply_html(update, f"❌ {exc}")
    except SQLAlchemyError:
        logger.exception("Database error while logging study for %s", user.id)
        await reply_html(update, "❌ A database error occurred. Please try again.")
    except Exception:
        logger.exception("Unexpected error in /log")
        raise


async def _goal_progress(telegram_id: int):
    """Return the :class:`GoalProgress` for a user (own session)."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return await goal_service.progress_for(session, telegram_id)


def _format_goal_progress(progress) -> str:
    """Render a :class:`GoalProgress` as an HTML progress block."""
    if progress.goal_hours <= 0:
        return "Set a target with /goal &lt;hours&gt;"
    bar = progress_bar(progress.logged_hours, progress.goal_hours)
    return (
        f"<code>{bar}</code>\n{human_hours(progress.logged_hours)}/"
        f"{human_hours(progress.goal_hours)}"
    )


@rate_limited()
async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set today's goal: /goal 5."""
    if not context.args:
        await reply_html(update, "Usage: /goal &lt;hours&gt;  (e.g. /goal 5)")
        return
    try:
        hours = parse_hours_token(context.args[0], field="goal")
        validate_goal_hours(hours)
    except ValidationError as exc:
        await reply_html(update, f"❌ {exc}")
        return

    user = update.effective_user
    try:
        async with session_scope() as session:
            await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )
            await goal_service.set_goal(session, user.id, hours)
        progress = _format_goal_progress(await _goal_progress(user.id))
        await reply_html(update, f"🎯 Daily goal set to <b>{human_hours(hours)}</b>!\n{progress}")
    except Exception:
        logger.exception("Error in /goal")
        raise


@rate_limited()
async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's goal progress bar."""
    user = update.effective_user
    progress = await _goal_progress(user.id)
    text = _format_goal_progress(progress)
    status = "✅ Goal reached!" if progress.completed and progress.goal_hours > 0 else ""
    await reply_html(update, f"📊 Today's progress\n{text}\n{status}")


@rate_limited()
async def cmd_editlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit the most recent log: /editlog <hours|subject <name>|note <text>>."""
    if not context.args:
        await reply_html(
            update,
            "Usage:\n/editlog &lt;hours&gt;\n/editlog subject &lt;name&gt;\n/editlog note &lt;text&gt;",
        )
        return

    user = update.effective_user
    try:
        async with session_scope() as session:
            hours = None
            subject = None
            note = None
            mode = context.args[0].lower()
            if mode == "subject":
                subject = sanitise_subject(" ".join(context.args[1:]))
            elif mode == "note":
                note = sanitise_note(" ".join(context.args[1:]))
            else:
                hours = parse_hours_token(context.args[0], field="hours")
                validate_hours(hours)

            result = await study_service.edit_last(
                session, user.id, hours=hours, subject=subject, note=note
            )
            if result is None:
                await reply_html(update, "You have no study logs to edit.")
                return
            log, _ = result
            leaderboard_service.clear_cache()
        await reply_html(
            update,
            f"✏️ Updated last log: <b>{human_hours(log.hours)}</b> of "
            f"<b>{escape_html(log.subject)}</b>",
        )
    except StudyServiceError as exc:
        await reply_html(update, f"❌ {exc}")
    except ValidationError as exc:
        await reply_html(update, f"❌ {exc}")
    except Exception:
        logger.exception("Error in /editlog")
        raise


@rate_limited()
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Undo the most recent log within the edit window."""
    user = update.effective_user
    try:
        async with session_scope() as session:
            result = await study_service.undo_last(session, user.id)
            if result is None:
                await reply_html(update, "You have no study logs to undo.")
                return
            log, _ = result
            leaderboard_service.clear_cache()
        await reply_html(
            update,
            f"↩️ Undid your last log ({human_hours(log.hours)} of "
            f"{escape_html(log.subject)}).",
        )
    except StudyServiceError as exc:
        await reply_html(update, f"❌ {exc}")
    except Exception:
        logger.exception("Error in /undo")
        raise
