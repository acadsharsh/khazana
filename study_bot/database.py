"""Async SQLAlchemy engine, session factory and bootstrap helpers.

The module exposes:

* :data:`engine`        - shared async engine bound to ``DATABASE_URL``
* :data:`AsyncSessionLocal` - factory producing :class:`AsyncSession` objects
* :func:`init_db`       - idempotently creates every table
* :func:`session_scope` - transactional async context manager
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import models  # noqa: F401  (importing registers every table on Base.metadata)
from config import settings
from models import Base

logger = logging.getLogger(__name__)


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent folder for a sqlite file database if missing."""
    if "sqlite" in url and ":memory:" not in url:
        path = url.split("///")[-1]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


_ensure_sqlite_directory(settings.database_url)

engine = create_async_engine(settings.database_url, echo=False, future=True)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    """Create all database tables if they do not already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialised (url=%s)", settings.database_url)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional session.

    The session is committed when the block exits cleanly and rolled back if
    any exception propagates out of it.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Async generator session provider (handy for dependency injection)."""
    async with AsyncSessionLocal() as session:
        yield session
