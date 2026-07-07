"""Centralised logging configuration with rotating file handlers.

Three rotating log files are produced under ``logs/``:

* ``bot.log``      - everything (at LOG_LEVEL)
* ``errors.log``   - WARNING and above
* ``scheduler.log``- output from the scheduler logger
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join("logs")
_FORMAT = "%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with console + rotating file handlers."""
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Reset any pre-existing handlers (idempotent / test friendly).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    bot_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "bot.log"),
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    bot_handler.setFormatter(formatter)
    root.addHandler(bot_handler)

    error_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "errors.log"),
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    # Dedicated scheduler log.
    scheduler_logger = logging.getLogger("scheduler")
    scheduler_logger.setLevel(logging.INFO)
    sched_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "scheduler.log"),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    sched_handler.setFormatter(formatter)
    scheduler_logger.addHandler(sched_handler)

    # Quiet down noisy libraries.
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
