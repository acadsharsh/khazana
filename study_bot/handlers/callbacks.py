"""Inline-keyboard callback handler (leaderboard scope / goal / pomodoro)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, session_scope
from handlers.gamification import SCOPE_LABELS
from handlers.pomodoro import cmd_startpomodoro
from keyboards.inline import leaderboard_scope_keyboard
from services import goal_service, leaderboard_service, user_service
from utils.formatting import escape_html, format_table, human_hours, movement_arrow

logger = logging.getLogger(__name__)


async def cmd_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline button callbacks based on their ``data`` prefix."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    user = query.from_user

    try:
        if data.startswith("lb:"):
            await _handle_leaderboard(query, data.split(":", 1)[1])
        elif data.startswith("goal:"):
            await _handle_goal(query, user.id, data.split(":", 1)[1])
        elif data.startswith("pomo:"):
            await _handle_pomodoro(query, context, user.id, data.split(":", 1)[1])
    except Exception:  # pragma: no cover
        logger.exception("Callback handler failed for data=%s", data)


async def _handle_leaderboard(query, scope: str) -> None:
    async with AsyncSessionLocal() as session:
        board = await leaderboard_service.get_leaderboard(session, scope)
    label = SCOPE_LABELS.get(board.scope, board.scope.title())
    if not board.entries:
        text = f"🏆 Leaderboard ({label})\nNo entries yet."
    else:
        rows = [
            [
                f"#{e.rank}{movement_arrow(e.movement)}",
                e.name[:16],
                human_hours(e.hours),
                f"Lv{e.level}",
            ]
            for e in board.entries[:15]
        ]
        text = (
            f"🏆 <b>Leaderboard — {label}</b>\n\n"
            + format_table(["#", "Member", "Hours", "Lvl"], rows)
        )
    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=leaderboard_scope_keyboard(),
    )


async def _handle_goal(query, telegram_id: int, raw_value: str) -> None:
    try:
        hours = float(raw_value)
    except ValueError:
        return
    async with session_scope() as session:
        await user_service.get_or_create_user(
            session, telegram_id, None, None
        )
        await goal_service.set_goal(session, telegram_id, hours)
        progress = await goal_service.progress_for(session, telegram_id)
    from utils.formatting import progress_bar

    bar = progress_bar(progress.logged_hours, progress.goal_hours)
    await query.edit_message_text(
        text=(
            f"🎯 Goal set to <b>{human_hours(hours)}</b>!\n"
            f"<code>{bar}</code>\n{human_hours(progress.logged_hours)}/"
            f"{human_hours(progress.goal_hours)}"
        ),
        parse_mode=ParseMode.HTML,
    )


async def _handle_pomodoro(query, context, telegram_id: int, raw_minutes: str) -> None:
    """Start a pomodoro from an inline button by reusing the command handler."""
    context.args = [raw_minutes]
    # Build a lightweight Update-like call by invoking the command logic.
    # query.message provides the chat context the handler expects.
    await cmd_startpomodoro.__wrapped__(query, context)  # type: ignore[attr-defined]
