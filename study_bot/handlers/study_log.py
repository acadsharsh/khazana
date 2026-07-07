"""Handlers: Zero-Command Global Gemini AI Router for Logging, Goals, and Progress."""
from __future__ import annotations

import logging
import json
import os
import httpx

from sqlalchemy.exc import SQLAlchemyError
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from database import session_scope
from services import gamification_service, goal_service, leaderboard_service, reminder_service, study_service, user_service
from services.study_service import StudyServiceError
from utils.formatting import escape_html, human_hours, progress_bar
from utils.helpers import rate_limited
from handlers.core import reply_html

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

async def _analyze_intent_with_gemini(text: str) -> dict:
    """Analyze the user text to find their real intent and structured data with robust error recovery."""
    api_key = getattr(settings, "gemini_api_key", None) or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY missing!")
        return {"intent": "chat", "reply": "Bhai backend pe AI key set nahi hai!"}

    prompt = f"""
    You are the brain of a student group tracking bot named "padhle bsdk".
    Analyze this message from a student: "{text}"

    Determine their intent and return a strictly valid JSON object. Do not include markdown code block syntax.
    
    Intents possible:
    1. "log": User is sharing what they studied (e.g., "math lec 5,6,7 aur chem lec 4,5").
       Extract an array of sessions. Estimate duration dynamically if only lecture numbers are given (assume ~1.5h per lecture).
       Example output format:
       {{"intent": "log", "data": [{{"subject": "Math", "hours": 4.5, "note": "Lectures 5,6,7"}}, {{"subject": "Chemistry", "hours": 3.0, "note": "Lectures 4,5"}}]}}

    2. "set_goal": User wants to set a target/goal for today.
       Example: "aaj ka target 6 hours" -> {{"intent": "set_goal", "hours": 6.0}}

    3. "check_progress": User wants to see their progress bar or status for today.
       Example: "mera progress dikhao", "kitna bacha hai aaj ka" -> {{"intent": "check_progress"}}

    4. "chat": Normal chat, rant, or motivation. Do not touch DB. Just provide a short, witty, highly encouraging Hinglish reply keeping the JEE/Class 12 context and group vibe ("padhle bsdk") alive.
       Example: {{"intent": "chat", "reply": "Padhle bhai, ncert lagane ka time aa gaya hai!"}}

    Return ONLY a raw valid JSON object.
    """

    try:
        async with httpx.AsyncClient() as client:
            # generationConfig enforces structural validation from the AI studio directly
            response = await client.post(
                f"{GEMINI_API_URL}?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"}
                },
                timeout=12.0
            )
            
            if response.status_code == 200:
                data = response.json()
                raw_json = data['candidates'][0]['content']['parts'][0]['text'].strip()
                
                # Dynamic stripping to clear any stray wrapper artifacts
                if "```" in raw_json:
                    raw_json = raw_json.replace("```json", "").replace("```", "").strip()
                
                logger.info(f"Gemini raw clean output response parsed: {raw_json}")
                return json.loads(raw_json)
            else:
                logger.error(f"Gemini API endpoint returned structural code: {response.status_code} - {response.text}")
                
    except Exception as e:
        logger.error(f"Gemini global router failed validation logic: {e}")
        
    return {"intent": "chat", "reply": "Kuch samajh nahi aaya bhai, thoda sa saaf likho na!"}


@rate_limited()
async def handle_global_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercepts every group message, evaluates intent via Gemini, and updates engine seamlessly."""
    user_text = update.message.text.strip() if update.message else None
    if not user_text:
        return

    user = update.effective_user
    await update.message.reply_chat_action("typing")

    # Get dynamic intent from Gemini
    ai_analysis = await _analyze_intent_with_gemini(user_text)
    intent = ai_analysis.get("intent", "chat")

    # --- INTENT: NORMAL CHAT/MOTIVATION ---
    if intent == "chat":
        reply_msg = ai_analysis.get("reply", "Padhai pe dhyan do bhai! 🎯")
        await reply_html(update, reply_msg)
        return

    try:
        async with session_scope() as session:
            db_user = await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )

            # --- INTENT: LOG STUDY SESSIONS ---
            if intent == "log":
                sessions_data = ai_analysis.get("data", [])
                if not sessions_data:
                    await reply_html(update, "🧠 AI ko koi specific studies nahi mili. Thoda clear batao!")
                    return

                master_confirmation_lines = []
                logged_any = False

                for entry in sessions_data:
                    sub_name = entry.get("subject", "General").strip()
                    
                    # Prevent casting type exceptions dynamically
                    try:
                        hours_val = float(entry.get("hours", 0))
                    except (ValueError, TypeError):
                        hours_val = 0.0
                        
                    note_val = entry.get("note", user_text)

                    if hours_val <= 0 or len(sub_name) <= 1:
                        continue

                    # Core DB operations wrapper
                    try:
                        result = await study_service.log_study(
                            session, db_user, sub_name, hours_val, note_val
                        )
                        logged_any = True
                        master_confirmation_lines.append(
                            f"✨ Logged <b>{human_hours(hours_val)}</b> in <b>{escape_html(sub_name)}</b> (+{result.xp_earned} XP)"
                        )
                    except Exception as db_err:
                        logger.error(f"Failed to save log to DB for {sub_name}: {db_err}")
                        await reply_html(update, f"⚠️ DB Save Error ({escape_html(sub_name)}): {escape_html(str(db_err))}")
                        return

                if not logged_any:
                    await reply_html(update, "❌ AI ne text padha par koi valid hours extract nahi ho paaye.")
                    return

                # Safely run hooks without halting the thread context
                try:
                    new_badges = await gamification_service.evaluate_user(session, db_user)
                    await reminder_service.mark_logged(session, db_user.telegram_id)
                    streak = db_user.current_streak
                    
                    progress_obj = await goal_service.progress_for(session, db_user.telegram_id)
                    progress = _format_goal_progress(progress_obj)
                    leaderboard_service.clear_cache()
                except Exception as post_err:
                    logger.error(f"Post-log processing crash: {post_err}")
                    progress = "Target progress calculation suspended."
                    streak = getattr(db_user, 'current_streak', 0)
                    new_badges = []

                summary_txt = "🧠 <b>Gemini AI Auto-Tracker</b>\n" + "\n".join(master_confirmation_lines)
                if new_badges:
                    badges = " ".join(f"{b.emoji} <b>{escape_html(b.name)}</b>" for b in new_badges)
                    summary_txt += f"\n🎖️ Achievements: {badges}"
                
                await reply_html(update, f"{summary_txt}\n\n🔥 Streak: {streak} days\n{progress}")

            # --- INTENT: SET DAILY GOAL ---
            elif intent == "set_goal":
                try:
                    hours_val = float(ai_analysis.get("hours", 0))
                except (ValueError, TypeError):
                    hours_val = 0.0

                if hours_val <= 0 or hours_val > 24:
                    await reply_html(update, "❌ Sahi ghante batao bhai (1 se 24 ke beech)!")
                    return
                
                await goal_service.set_goal(session, user.id, hours_val)
                progress_obj = await goal_service.progress_for(session, user.id)
                progress = _format_goal_progress(progress_obj)
                await reply_html(update, f"🎯 Daily goal set to <b>{human_hours(hours_val)}</b>!\n{progress}")

            # --- INTENT: CHECK PROGRESS ---
            elif intent == "check_progress":
                progress_obj = await goal_service.progress_for(session, user.id)
                text_bar = _format_goal_progress(progress_obj)
                status = "✅ Goal reached!" if progress_obj and getattr(progress_obj, 'completed', False) and progress_obj.goal_hours > 0 else ""
                await reply_html(update, f"📊 Today's progress\n{text_bar}\n{status}")

    except Exception as e:
        logger.exception("Error inside global AI engine execution")
        # Direct debugging report right in the active layout
        await reply_html(update, f"⚠️ Runtime Error: <code>{escape_html(str(e))}</code>\nCheck Render logs for traceback.")


def _format_goal_progress(progress) -> str:
    """Formats the progress bar object safely."""
    if not progress or progress.goal_hours <= 0:
        return "Target set nahi hai bhai."
    bar = progress_bar(progress.logged_hours, progress.goal_hours)
    return f"<code>{bar}</code>\n{human_hours(progress.logged_hours)}/{human_hours(progress.goal_hours)}"
