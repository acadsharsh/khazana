"""Handlers: /startpomodoro, /cancelpomodoro and the completion job."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, session_scope
from handlers.core import reply_html
from models import FocusSession
from services import gamification_service, leaderboard_service, reminder_service, study_service, user_service
from utils.formatting import bold, escape_html, human_hours
from utils.helpers import rate_limited
from utils.validation import ValidationError, parse_hours_token, sanitise_subject

logger = logging.getLogger(__name__)

# Focus sessions longer than this are rejected (minutes).
MAX_POMODORO_MINUTES = 180


@rate_limited()
async def cmd_startpomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a pomodoro/focus timer.

    Usage: /startpomodoro 25 [subject] [auto]
    When ``auto`` is supplied, the session is logged as study time on completion.
    """
    if not context.args:
        await reply_html(update, "🍅 Usage: /startpomodoro &lt;minutes&gt; [subject] [auto]")
        return
    try:
        minutes = int(parse_hours_token(context.args[0], field="minutes"))
    except ValidationError as exc:
        await reply_html(update, f"❌ {exc}")
        return
    except ValueError:
        await reply_html(update, "❌ Minutes must be a whole number.")
        return
    if minutes <= 0 or minutes > MAX_POMODORO_MINUTES:
        await reply_html(update, f"❌ Choose a duration between 1 and {MAX_POMODORO_MINUTES} minutes.")
        return

    tokens = context.args[1:]
    auto = any(t.lower() in {"auto", "log"} for t in tokens)
    subject_tokens = [t for t in tokens if t.lower() not in {"auto", "log"}]
    subject = sanitise_subject(" ".join(subject_tokens)) if subject_tokens else None

    user = update.effective_user
    chat_id = update.effective_chat.id
    scheduler = context.bot_data.get("scheduler")

    try:
        async with session_scope() as session:
            await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )
            session_row = FocusSession(
                telegram_id=user.id,
                duration=minutes,
                subject=subject,
                started_at=datetime.utcnow(),
                status="running",
            )
            session.add(session_row)
            await session.flush()
            session_id = session_row.id

        if scheduler is not None:
            run_at = datetime.utcnow() + timedelta(minutes=minutes)
            scheduler.add_job(
                pomodoro_complete,
                "date",
                run_date=run_at,
                args=[chat_id, user.id, session_id, minutes, subject, auto],
                id=f"pomo_{session_id}",
                replace_existing=True,
            )

        extra = f" of <b>{escape_html(subject)}</b>" if subject else ""
        auto_note = " It will be auto-logged on completion. ✅" if auto else ""
        await reply_html(
            update,
            f"🍅 Focus session started: <b>{minutes} min</b>{extra}!\n"
            f"I'll ping you when it's done.{auto_note}\n"
            f"Use /cancelpomodoro to stop early.",
        )
    except Exception:
        logger.exception("Error starting pomodoro for %s", user.id)
        raise


@rate_limited()
async def cmd_cancelpomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a running focus session."""
    user = update.effective_user
    scheduler = context.bot_data.get("scheduler")
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(FocusSession)
                .where(FocusSession.telegram_id == user.id, FocusSession.status == "running")
                .order_by(FocusSession.started_at.desc())
                .limit(1)
            )
            focus = result.scalar_one_or_none()
            if focus is None:
                await reply_html(update, "🍅 You have no running focus session.")
                return
            focus.status = "cancelled"
            focus.completed_at = datetime.utcnow()
            session_id = focus.id
        if scheduler is not None:
            try:
                scheduler.remove_job(f"pomo_{session_id}")
            except Exception:
                pass
        await reply_html(update, "🛑 Focus session cancelled.")
    except Exception:
        logger.exception("Error cancelling pomodoro for %s", user.id)
        raise


async def pomodoro_complete(
    chat_id: int,
    telegram_id: int,
    session_id: int,
    minutes: int,
    subject,
    auto_log: bool,
) -> None:
    """Scheduled job: mark the focus session complete and optionally log it.

    Uses the global bot instance captured at scheduler setup time.
    """
    from scheduler import get_bot

    bot = get_bot()
    try:
        async with session_scope() as session:
            focus = await session.get(FocusSession, session_id)
            if focus is None or focus.status != "running":
                return
            focus.status = "completed"
            focus.completed_at = datetime.utcnow()

            log_note = None
            if auto_log and subject:
                db_user = await user_service.get_user(session, telegram_id)
                if db_user is not None:
                    hours = round(minutes / 60.0, 2)
                    try:
                        result = await study_service.log_study(
                            session, db_user, subject, hours, "Pomodoro auto-log"
                        )
                        new_badges = await gamification_service.evaluate_user(session, db_user)
                        await reminder_service.mark_logged(session, telegram_id)
                        focus.auto_logged = True
                        log_note = (
                            f"\n✅ Auto-logged {human_hours(hours)} of "
                            f"{escape_html(subject)} (+{result.xp_earned} XP)"
                        )
                        if new_badges:
                            log_note += " 🎖️ " + " ".join(
                                f"{b.emoji}{escape_html(b.name)}" for b in new_badges
                            )
                    except study_service.StudyServiceError as exc:
                        log_note = f"\n⚠️ Auto-log skipped: {exc}"
            leaderboard_service.clear_cache()

        if bot is not None:
            subject_text = f" of <b>{escape_html(subject)}</b>" if subject else ""
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 <b>Pomodoro complete!</b> {minutes} min{subject_text} of focused "
                    f"work done. Take a short break. ☕{log_note or ''}"
                ),
                parse_mode="HTML",
            )
    except Exception:
        logger.exception("Pomodoro completion job failed for session %s", session_id)
