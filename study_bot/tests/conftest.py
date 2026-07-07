"""Shared pytest fixtures.

A fresh in-memory SQLite database (StaticPool) is created for every test and
wired into every module that reads ``database.AsyncSessionLocal`` so handlers
can be exercised end-to-end without a live Telegram connection.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import database
from handlers import admin, analytics, callbacks, core, gamification, partners, pomodoro, study_log
from models import Base
from tests.fakes import FakeBot, FakeChat, FakeContext, FakeMessage, FakeUpdate, FakeUser  # noqa: F401

_MODULES_WITH_SESSIONLOCAL = [
    database,
    core,
    analytics,
    gamification,
    study_log,
    admin,
    partners,
    pomodoro,
    callbacks,
]


@pytest_asyncio.fixture
async def session_factory():
    """Create an isolated in-memory DB and patch every consumer of it."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    originals = {
        mod: getattr(mod, "AsyncSessionLocal", None) for mod in _MODULES_WITH_SESSIONLOCAL
    }
    for mod in _MODULES_WITH_SESSIONLOCAL:
        mod.AsyncSessionLocal = factory

    yield factory

    for mod, original in originals.items():
        if original is not None:
            mod.AsyncSessionLocal = original
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory):
    """A session bound to the test database."""
    async with session_factory() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the in-memory rate limiter between tests so they never throttle."""
    from utils.helpers import default_limiter

    default_limiter._hits.clear()
    yield
    default_limiter._hits.clear()
