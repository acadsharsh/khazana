"""Telegram bot entry point.

Wires handlers, the database and the scheduler into a single
:class:`telegram.ext.Application` and runs it with long polling.
"""
from __future__ import annotations

import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

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

    app.add_handler(CallbackQueryHandler(callbacks.cmd_callback))
    app.add_handler(MessageHandler(filters.COMMAND, core.unknown_command))
    app.add_error_handler(core.error_handler)


def create_application() -> Application:
    """Build and configure the Telegram application."""
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    _register_handlers(app)
    return app


class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")


def run_dummy_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    logger.info(f"Starting dummy server on port {port} for Render...")
    server.serve_forever()


def main() -> None:
    """Entry point: configure logging and start long polling."""
    setup_logging(settings.log_level)
    logger.info("Starting Study Accountability Bot...")
    if not settings.bot_token:
        logger.error(
            "BOT_TOKEN is not set. Copy .env.example to .env and configure it."
        )
        return
    
    # Render binding ke liye dummy server initiation
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = create_application()

    # Python 3.14+ compatibility ke liye explicitly main thread me standard loop register kar dena
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Ab run_polling standard tareeke se chalega kyunki peeche event loop activate ho chuka hai
    logger.info("Invoking secure run_polling sequence...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        close_loop=False,  # loop close hone se async tasks destroy hone ka error khatam hoga
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )


if __name__ == "__main__":
    main()
