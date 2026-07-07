"""Telegram bot entry point.

Wires handlers, the database and the scheduler into a single
:class:`telegram.ext.Application` and runs it with long polling.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import settings
from database import init_db
from handlers import admin, analytics, callbacks, core, gamification, partners, pomodoro, study_log
from logging_config import setup_logging
from scheduler import SchedulerManager, load_runtime_overrides

logger = logging.getLogger(__name__)

COMMAND_DESCRIPTIONS = {
    "start": "Register & show the menu",
    "help": "List every command",
    "log": "Log study time: /log Math 2",
    "goal": "Set today's goal: /goal 5",
    "progress": "Today's goal progress",
    "stats": "Your full statistics",
    "streak": "Current & longest streak",
    "leaderboard": "Rankings (daily/weekly/monthly/alltime)",
    "rank": "Your leaderboard position",
    "badges": "Achievements & badges",
    "subjects": "Hours by subject",
    "calendar": "30-day study calendar",
    "focuscheck": "Group focus summary",
    "startpomodoro": "Start a focus timer",
    "partner": "Set an accountability partner",
}


async def post_init(application: Application) -> None:
    """Initialise DB, runtime overrides, bot commands and the scheduler."""
    await init_db()
    await load_runtime_overrides()

    try:
        await application.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc in COMMAND_DESCRIPTIONS.items()]
        )
    except Exception:  # pragma: no cover
        logger.warning("Could not set bot commands menu.")

    scheduler = SchedulerManager(application)
    scheduler.setup()
    application.bot_data["scheduler"] = scheduler.scheduler
    scheduler.start()
    logger.info("Application initialised; scheduler started.")


async def post_shutdown(application: Application) -> None:
    """Gracefully stop the scheduler on shutdown."""
    scheduler = application.bot_data.get("scheduler")
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover
            pass


def _register_handlers(app: Application) -> None:
    """Register every update handler with sensible grouping."""
    # Group -1: auto-register any user before their command is processed.
    app.add_handler(MessageHandler(filters.ALL, core.auto_register), group=-1)

    commands = [
        ("start", core.cmd_start),
        ("help", core.cmd_help),
        ("menu", core.cmd_menu),
        ("log", study_log.cmd_log),
        ("editlog", study_log.cmd_editlog),
        ("undo", study_log.cmd_undo),
        ("goal", study_log.cmd_goal),
        ("progress", study_log.cmd_progress),
        ("streak", gamification.cmd_streak),
        ("leaderboard", gamification.cmd_leaderboard),
        ("rank", gamification.cmd_rank),
        ("stats", gamification.cmd_stats),
        ("badges", gamification.cmd_badges),
        ("subjects", analytics.cmd_subjects),
        ("showsubject", analytics.cmd_show_subject),
        ("calendar", analytics.cmd_calendar),
        ("focuscheck", analytics.cmd_focuscheck),
        ("startpomodoro", pomodoro.cmd_startpomodoro),
        ("cancelpomodoro", pomodoro.cmd_cancelpomodoro),
        ("partner", partners.cmd_partner),
        ("partners", partners.cmd_partners),
        ("removepartner", partners.cmd_removepartner),
        # Admin-only (the @admin_only guard enforces access)
        ("adminstats", admin.cmd_adminstats),
        ("export", admin.cmd_export),
        ("broadcast", admin.cmd_broadcast),
        ("listusers", admin.cmd_listusers),
        ("resetuser", admin.cmd_resetuser),
        ("removeuser", admin.cmd_removeuser),
        ("setthreshold", admin.cmd_setthreshold),
        ("backup", admin.cmd_backup),
        ("restore", admin.cmd_restore),
    ]
    for name, func in commands:
        app.add_handler(CommandHandler(name, func))

    # Inline keyboard callbacks.
    app.add_handler(CallbackQueryHandler(callbacks.cmd_callback))

    # Catch-all for any unrecognised command (after the real handlers).
    app.add_handler(MessageHandler(filters.COMMAND, core.unknown_command))

    app.add_error_handler(core.error_handler)


def create_application() -> Application:
    """Build and configure the Telegram application (reused by tests)."""
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    _register_handlers(app)
    return app


def main() -> None:
    """Entry point: configure logging and start long polling."""
    setup_logging(settings.log_level)
    logger.info("Starting Study Accountability Bot...")
    if not settings.bot_token:
        logger.error(
            "BOT_TOKEN is not set. Copy .env.example to .env and configure it."
        )
        return
    app = create_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
