"""Inline / reply keyboard factories for the Telegram UI."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """A persistent reply keyboard exposing the most common commands."""
    keyboard = [
        ["📊 /stats", "🔥 /streak", "📅 /progress"],
        ["🏆 /leaderboard", "🎖️ /badges", "🥇 /rank"],
        ["📚 /subjects", "🗓️ /calendar", "🍅 /startpomodoro 25"],
    ]
    return ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=False
    )


def leaderboard_scope_keyboard() -> InlineKeyboardMarkup:
    """Buttons to switch the leaderboard time scope."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Today", callback_data="lb:daily"),
                InlineKeyboardButton("Week", callback_data="lb:weekly"),
            ],
            [
                InlineKeyboardButton("Month", callback_data="lb:monthly"),
                InlineKeyboardButton("All Time", callback_data="lb:alltime"),
            ]
        ]
    )


def goal_keyboard() -> InlineKeyboardMarkup:
    """Quick-set daily goal presets."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("2h", callback_data="goal:2"),
                InlineKeyboardButton("3h", callback_data="goal:3"),
                InlineKeyboardButton("5h", callback_data="goal:5"),
            ],
            [InlineKeyboardButton("8h", callback_data="goal:8")],
        ]
    )


def pomodoro_keyboard() -> InlineKeyboardMarkup:
    """Quick-start pomodoro presets."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🍅 25 min", callback_data="pomo:25"),
                InlineKeyboardButton("🍅 45 min", callback_data="pomo:45"),
                InlineKeyboardButton("🍅 50 min", callback_data="pomo:50"),
            ]
        ]
    )
