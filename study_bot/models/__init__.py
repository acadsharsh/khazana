"""SQLAlchemy ORM models for every persisted entity.

Every model uses the modern SQLAlchemy 2.0 ``Mapped`` / ``mapped_column``
syntax with explicit type hints.  Tables are created automatically by
:func:`database.init_db`.

Entities
--------
* :class:`User`                 - registered members and their gamification stats
* :class:`StudyLog`             - individual study session records
* :class:`DailyGoal`            - per-day hourly targets
* :class:`Achievement`          - awarded badges / milestones
* :class:`Setting`              - generic key/value store + leaderboard snapshots
* :class:`ReminderStatus`       - tracks which reminders a user already received
* :class:`FocusSession`         - pomodoro / focus timer records
* :class:`AccountabilityPartner`- pairing of members who hold each other accountable
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


class User(Base):
    """A registered Telegram member."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    join_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    total_study_hours: Mapped[float] = mapped_column(Float, default=0.0)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=0)
    #: JSON-encoded list of badge codes (kept as Text for portability)
    badges: Mapped[str] = mapped_column(Text, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    goals_completed: Mapped[int] = mapped_column(Integer, default=0)

    logs: Mapped[List["StudyLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.telegram_id} ({self.display_name})>"


class StudyLog(Base):
    """A single study session recorded by a user."""

    __tablename__ = "study_logs"
    __table_args__ = (
        Index("ix_studylogs_user_date", "telegram_id", "log_date"),
        Index("ix_studylogs_user_subject", "telegram_id", "subject"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subject: Mapped[str] = mapped_column(String(64), default="General", index=True)
    hours: Mapped[float] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    log_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    week_number: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)
    editable_until: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="logs")


class DailyGoal(Base):
    """A user's hourly target for a specific day."""

    __tablename__ = "daily_goals"
    __table_args__ = (
        UniqueConstraint("telegram_id", "goal_date", name="uq_goal_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    goal_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    goal_hours: Mapped[float] = mapped_column(Float, default=0.0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)


class Achievement(Base):
    """A badge or milestone awarded to a user."""

    __tablename__ = "achievements"
    __table_args__ = (
        UniqueConstraint("telegram_id", "code", name="uq_achievement_user_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    achieved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Setting(Base):
    """Generic key/value store (also persists leaderboard rank snapshots)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReminderStatus(Base):
    """Tracks reminder delivery state for a user on a given day."""

    __tablename__ = "reminder_status"
    __table_args__ = (
        UniqueConstraint("telegram_id", "reminder_date", name="uq_reminder_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    reminder_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    morning_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    afternoon_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    evening_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    logged_today: Mapped[bool] = mapped_column(Boolean, default=False)


class FocusSession(Base):
    """A pomodoro / focus timer session."""

    __tablename__ = "focus_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    duration: Mapped[int] = mapped_column(Integer)  # minutes
    subject: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    #: running | completed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="running")
    auto_logged: Mapped[bool] = mapped_column(Boolean, default=False)


class AccountabilityPartner(Base):
    """A pair of members who hold each other accountable."""

    __tablename__ = "accountability_partners"
    __table_args__ = (
        UniqueConstraint("telegram_id", "partner_telegram_id", name="uq_partner_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    partner_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    partner_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


__all__ = [
    "Base",
    "User",
    "StudyLog",
    "DailyGoal",
    "Achievement",
    "Setting",
    "ReminderStatus",
    "FocusSession",
    "AccountabilityPartner",
]
