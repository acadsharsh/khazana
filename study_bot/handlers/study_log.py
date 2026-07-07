"""Handlers: /log, /editlog, /undo, /goal, /progress with Advanced Gemini AI Parsing."""
from __future__ import annotations

import logging
import os
import json
from datetime import datetime
import httpx

from sqlalchemy.exc import SQLAlchemyError
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from database import session_scope
from services import gamification_service, goal_service, leaderboard_service, reminder_service, study_service, user_service
from services.study_service import StudyServiceError
from utils.formatting import bold, escape_html, human_hours, progress_bar
from utils.helpers import rate_limited
from utils.validation import (
    ParsedLog,
    ValidationError,
    parse_hours_token,
    sanitise_note,
    sanitise_subject,
    validate_goal_hours,
    validate_hours,
)
from handlers.core import reply_html

logger = logging.getLogger(__name__)

# Gemini API Endpoint Configuration
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


async def _parse_with_gemini(text: str) -> list[dict]:
    """Use Gemini AI to extract structured subjects, hours, and notes from raw paragraph."""
    if not settings.bot_token:  # Just a safety check, we need settings data
        return []
        
    # We assume GEMINI_API_KEY is placed in settings or environment variable
    # If not in settings, you can add GEMINI_API_KEY="your_key" in your .env file
    api_key = getattr(settings, "gemini_api_key", None) or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY missing! Falling back to standard processing.")
        return []

    prompt = f"""
    You are an expert AI data extractor for a student study tracking bot.
    Analyze the following user's raw input paragraph and extract all study sessions.
    
    User Input: "{text}"
    
    Convert it into a strictly valid JSON array of objects. Each object MUST have:
    1. "subject": Cleaned short name of the subject (e.g., "Math", "Physics", "Chemistry", "ITF").
    2. "hours": The float/integer number of hours spent on that subject. (Convert phrases like "4 hrs 30 mins" to 4.5, "1 hr" to 1.0).
    3. "note": A concise summary of specific tasks/topics done for that subject from the text (e.g. "Math hw done, lectures 5,6,7").

    Return ONLY the raw valid JSON block array. Do not include markdown code block ticks (```json).
    If no clear study log exists, return an empty array [].
    """

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={api_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()
                raw_json = data['candidates'][0]['content']['parts'][0]['text'].strip()
                # Clean accidental markdown wrapping if any
                if raw_json.startswith("```"):
                    raw_json = raw_json.split("```")[1].replace("json", "").strip()
                return json.loads(raw_json)
    except Exception as e:
        logger.error(f"Gemini processing failed: {e}")
    return []


def _log_confirmation(parsed_subject: str, hours: float, result, new_badges) -> str:
    lines = [
        f"✅ Logged <b>{human_hours(hours)}</b> of "
        f"<b>{escape_html(parsed_subject)}</b>",
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
    """Log a study session: Advanced AI processes paragraphs into individual subject records."""
    user_raw_text = " ".join(context.args)
    if not user_raw_text:
        await reply_html(update, "❌ Usage: Kuch toh likho bhai! \nFormat: <code>/log Math 2 hours, chem lec 4</code>")
        return

    user = update.effective_user
    await update.message.reply_chat_action("typing")

    # Call Gemini AI for extraction
    ai_logs = await _parse_with_gemini(user_raw_text)

    # Fallback Option: If AI parsing fails, try primitive manual extraction to keep bot active
    if not ai_logs:
        import re
        pattern = r'([a-zA-Z\s_]+)\s+(\d+(?:\.\d+)?)(?:\s*(?:hours|hour|hrs|hr))?'
        matches = re.findall(pattern, user_raw_text, re.IGNORECASE)
        for m in matches:
            ai_logs.append({"subject": m[0].strip(), "hours": float(m[1]), "note": user_raw_text})

    if not ai_logs:
        await reply_html(update, "❌ AI is unable to parse any valid subject and study hours from your context. Try again clearly.")
        return

    logged_any = False
    master_confirmation_lines = []

    try:
        async with session_scope() as session:
            db_user = await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )

            for session_entry in ai_logs:
                sub_name = session_entry.get("subject", "General").strip()
                hours_val = float(session_entry.get("hours", 0))
                note_val = session_entry.get("note", user_raw_text)

                if hours_val <= 0 or len(sub_name) <= 1:
                    continue

                subject = sanitise_subject(sub_name)
                note = sanitise_note(note_val)

                # Direct database writing using core business rules
                result = await study_service.log_study(
                    session, db_user, subject, hours_val, note
                )
                logged_any = True
                
                # Stack up individual confirmations
                master_confirmation_lines.append(
                    f"✨ Logged <b>{human_hours(hours_val)}</b> in <b>{escape_html(subject)}</b> (+{result.xp_earned} XP)"
                )

            if not logged_any:
                await reply_html(update, "❌ Configuration matching yielded 0 hours tracked.")
                return

            new_badges = await gamification_service.evaluate_user(session, db_user)
            await reminder_service.mark_logged(session, db_user.telegram_id)
            
            streak = db_user.current_streak
            progress = _format_goal_progress(await _goal_progress(db_user.telegram_id))

        leaderboard_service.clear_cache()
        
        # Display super aesthetic dashboard response
        summary_txt = "🧠 <b>Gemini AI Study Tracker</b>\n" + "\n".join(master_confirmation_lines)
        if new_badges:
            badges = " ".join(f"{b.emoji} <b>{escape_html(b.name)}</b>" for b in new_badges)
            summary_txt += f"\n🎖️ Achievements: {badges}"
            
        await reply_html(update, f"{summary_txt}\n\n🔥 Streak: {streak} days\n{progress}")

    except StudyServiceError as exc:
        await reply_html(update, f"❌ {exc}")
    except SQLAlchemyError:
        logger.exception("Database error while logging study for %s", user.id)
        await reply_html(update, "❌ Database operations failed.")
    except Exception:
        logger.exception("Unexpected error in AI /log")
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
    """Edit the most recent log."""
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
