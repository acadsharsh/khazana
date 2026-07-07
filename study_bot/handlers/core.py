"""Core handlers: auto-registration, /start, /help, errors and the menu.

The auto-registration handler runs in update group ``-1`` so it always executes
before the command handlers in group ``0`` - this guarantees every interacting
user is persisted before their command is processed.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import settings
from database import AsyncSessionLocal
from keyboards.inline import main_menu_keyboard
from services import user_service
from utils.formatting import bold, escape_html, truncate

logger = logging.getLogger(__name__)

HELP_TEXT = """
<b>📚 Study Accountability Bot</b>

<b>Logging & goals</b>
/log &lt;hours&gt; - log study time (e.g. /log 3)
/log &lt;subject&gt; &lt;hours&gt; [note] - e.g. /log Math 2 finished DP
/goal &lt;hours&gt; - set today's target
/progress - view today's goal progress
/editlog &lt;hours|subject|note&gt; - edit your last log (10 min)
/undo - undo your last log (10 min)

<b>Stats & gamification</b>
/stats - your full statistics
/streak - current & longest streak
/leaderboard [daily|weekly|monthly|alltime]
/rank - your leaderboard position
/badges - achievements & badges

<b>Analytics</b>
/subjects - hours by subject
/show&lt;subject&gt; - e.g. /showmath
/calendar - 30-day study calendar
/focuscheck - group focus summary

<b>Focus & partners</b>
/startpomodoro &lt;minutes&gt; [subject] - focus timer
/cancelpomodoro - cancel running timer
/partner @username - set accountability partner
/partners - list your partners

<b>Admin</b>
/adminstats • /export csv|json|excel • /broadcast
/listusers • /resetuser • /removeuser • /setthreshold
/backup • /restore

XP: 10 per hour • Level = √(XP/100)
"""


async def reply_html(update: Update, text: str, **kwargs) -> None:
    """Send an HTML message, capping length to Telegram's 4096 char limit."""
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        truncate(text, 4000), parse_mode=ParseMode.HTML, **kwargs
    )


async def auto_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Persist any user the moment they interact with the bot."""
    user = update.effective_user
    if user is None:
        return
    try:
        async with AsyncSessionLocal() as session:
            await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )
            await session.commit()
    except Exception:  # pragma: no cover - never let registration crash a command
        logger.exception("Auto-registration failed for user %s", user.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message + main menu."""
    user = update.effective_user
    name = escape_html(user.full_name) if user else "there"
    text = (
        f"👋 Welcome <b>{name}</b>!\n\n"
        "I'm your study group accountability buddy. Log your study time, "
        "track streaks, earn XP & badges, and climb the leaderboard.\n\n"
        "Start with: <code>/log 2</code> or <code>/log Math 2</code>\n\n"
        "Type /help to see every command."
    )
    await update.effective_message.reply_text(
        truncate(text, 4000),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(update, HELP_TEXT)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-show the main reply keyboard."""
    await update.effective_message.reply_text(
        "📱 Main menu:",
        reply_markup=main_menu_keyboard(),
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for unrecognised commands."""
    await reply_html(
        update,
        "🤔 Unknown command. Type <b>/help</b> to see what I can do.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler - logs everything and informs the user gracefully."""
    logger.error("Unhandled exception", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message is not None:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong on my side. The error has been logged; "
                "please try again in a moment.",
            )
    except Exception:  # pragma: no cover
        logger.exception("Failed to send error notification to user")
