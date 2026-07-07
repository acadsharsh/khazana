"""Handlers: /streak, /leaderboard, /rank, /stats, /badges."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, session_scope
from handlers.core import reply_html
from services import analytics_service, gamification_service, leaderboard_service, user_service
from utils.formatting import (
    bold,
    emoji_badge,
    escape_html,
    format_table,
    human_hours,
    movement_arrow,
    progress_bar,
)
from utils.helpers import rate_limited

logger = logging.getLogger(__name__)

SCOPE_LABELS = {
    "daily": "Today",
    "weekly": "This Week",
    "monthly": "This Month",
    "alltime": "All Time",
}


@rate_limited()
async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current & longest streak."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        db_user = await user_service.get_user(session, user.id)
    if db_user is None:
        await reply_html(update, "Use /start to register first.")
        return
    bar = progress_bar(min(db_user.current_streak, 30), 30)
    text = (
        f"🔥 <b>Streak</b>\n\n"
        f"<code>{bar}</code>\n"
        f"Current: <b>{db_user.current_streak}</b> days\n"
        f"Longest: <b>{db_user.longest_streak}</b> days"
    )
    await reply_html(update, text)


@rate_limited()
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the leaderboard for a scope (default weekly)."""
    scope = (context.args[0].lower() if context.args else "weekly")
    async with AsyncSessionLocal() as session:
        board = await leaderboard_service.get_leaderboard(session, scope)

    label = SCOPE_LABELS.get(board.scope, board.scope.title())
    if not board.entries:
        await reply_html(update, f"🏆 Leaderboard ({label})\nNo entries yet. Be the first!")
        return

    rows = [
        [
            f"#{e.rank}{movement_arrow(e.movement)}",
            e.name[:16],
            human_hours(e.hours),
            f"{e.xp}",
            f"Lv{e.level}",
        ]
        for e in board.entries[:15]
    ]
    table = format_table(["#", "Member", "Hours", "XP", "Lvl"], rows)
    await reply_html(update, f"🏆 <b>Leaderboard — {label}</b>\n\n{table}")


@rate_limited()
async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's rank across all scopes."""
    user = update.effective_user
    scope = (context.args[0].lower() if context.args else "weekly")
    async with AsyncSessionLocal() as session:
        entry = await leaderboard_service.rank_of(session, user.id, scope)
        total = len((await leaderboard_service.get_leaderboard(session, scope)).entries)

    if entry is None:
        await reply_html(update, "You're not on this leaderboard yet - log some study time!")
        return
    await reply_html(
        update,
        f"🥇 Your rank ({SCOPE_LABELS.get(scope, scope.title())}): "
        f"<b>#{entry.rank}</b> of {total}\n"
        f"Hours: {human_hours(entry.hours)} • XP: {entry.xp} • "
        f"Level: {entry.level} {movement_arrow(entry.movement)}",
    )


@rate_limited()
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full personal statistics."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        db_user = await user_service.get_user(session, user.id)
    if db_user is None:
        await reply_html(update, "Use /start to register first.")
        return
    stats = await analytics_service.user_stats(session, db_user)

    calendar_lines = _render_calendar(stats["calendar"])
    next_badge = gamification_service.progress_to_next_hours_badge(db_user)
    badge_hint = ""
    if next_badge:
        defn, ratio = next_badge
        badge_hint = (
            f"\n🎯 Next badge: {defn.emoji} {escape_html(defn.name)} "
            f"({human_hours(db_user.total_study_hours)}/{human_hours(defn.threshold)})"
        )

    text = (
        f"📊 <b>Statistics — {escape_html(stats['display_name'])}</b>\n\n"
        f"⏱ Today: {bold(human_hours(stats['today']))}  |  "
        f"Week: {bold(human_hours(stats['week']))}  |  "
        f"Month: {bold(human_hours(stats['month']))}\n"
        f"📚 Lifetime: {bold(human_hours(stats['lifetime']))}  |  "
        f"Avg/day: {bold(human_hours(stats['average']))}\n\n"
        f"🔥 Streak: {bold(str(stats['current_streak']))}d "
        f"(best {stats['longest_streak']}d)\n"
        f"⚡ XP: {bold(str(stats['xp']))}  |  "
        f"Level: {bold(str(stats['level']))} {emoji_badge(stats['level'])}\n"
        f"🎖️ Achievements: {bold(str(stats['achievement_count']))}  |  "
        f"Goals done: {bold(str(stats['goals_completed']))}\n"
        f"📖 Favorite subject: {bold(escape_html(stats['favorite_subject']))}\n"
        f"🛡️ Badges: {stats['badges']}{badge_hint}\n\n"
        f"<b>🗓️ Last 30 days</b>\n<pre>{calendar_lines}</pre>"
    )
    await reply_html(update, text)


def _render_calendar(calendar) -> str:
    """Render a compact calendar grid: 🟩 logged, 🟥 missed, 🟦 today."""
    icons = []
    for day, hours in calendar:
        if day == calendar[-1][0]:
            icons.append("🟦" if hours <= 0 else "🟩")
        else:
            icons.append("🟩" if hours > 0 else "🟥")
    weeks = ["".join(icons[i : i + 7]) for i in range(0, len(icons), 7)]
    return "\n".join(weeks)


@rate_limited()
async def cmd_badges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List earned and locked achievements."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        earned = {a.code for a in await gamification_service.user_achievements(session, user.id)}
    lines = [bold("🎖️ Achievements")]
    for defn in gamification_service.all_definitions():
        mark = "✅" if defn.code in earned else "🔒"
        lines.append(f"{mark} {defn.emoji} {escape_html(defn.name)} — {escape_html(defn.description)}")
    await reply_html(update, "\n".join(lines))
