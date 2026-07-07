"""Builders for the daily, weekly and monthly scheduled reports.

Every builder takes an :class:`AsyncSession`, performs the required
aggregation queries and returns a Telegram-HTML string ready to be sent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Achievement, StudyLog, User
from services import (
    analytics_service,
    gamification_service,
    leaderboard_service,
    reminder_service,
    study_service,
)
from services.user_service import display_name_of
from utils.formatting import (
    bold,
    emoji_badge,
    escape_html,
    format_table,
    hr,
    human_hours,
)
from utils.time_utils import (
    date_range,
    days_ago,
    last_n_days,
    start_of_week,
    today_utc,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
async def _all_active_users(session: AsyncSession) -> List[User]:
    result = await session.execute(select(User).where(User.is_active.is_(True)))
    return list(result.scalars().all())


async def _subject_totals(
    session: AsyncSession, start: date, end: date
) -> Dict[str, float]:
    result = await session.execute(
        select(StudyLog.subject, func.sum(StudyLog.hours))
        .where(StudyLog.log_date >= start, StudyLog.log_date <= end)
        .group_by(StudyLog.subject)
        .order_by(func.sum(StudyLog.hours).desc())
    )
    return {s: float(h) for s, h in result.all()}


async def _per_day_totals(
    session: AsyncSession, start: date, end: date
) -> Dict[date, float]:
    result = await session.execute(
        select(StudyLog.log_date, func.sum(StudyLog.hours))
        .where(StudyLog.log_date >= start, StudyLog.log_date <= end)
        .group_by(StudyLog.log_date)
    )
    return {d: float(h) for d, h in result.all()}


# ---------------------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------------------
async def build_daily_report(session: AsyncSession) -> str:
    """📊 Daily Study Report (sorted by today's hours, desc)."""
    today = today_utc()
    week_start = start_of_week(today)
    month_start = today.replace(day=1)

    users = await _all_active_users(session)
    rows = []
    for user in users:
        today_h = await study_service.hours_today(session, user.telegram_id)
        week_h = await study_service.hours_in_range(
            session, user.telegram_id, week_start, today
        )
        month_h = await study_service.hours_in_range(
            session, user.telegram_id, month_start, today
        )
        efficiency = await analytics_service.efficiency_for(session, user)
        rows.append(
            {
                "user": user,
                "today": today_h,
                "week": week_h,
                "month": month_h,
                "efficiency": efficiency,
                "streak": user.current_streak,
            }
        )

    rows.sort(key=lambda r: r["today"], reverse=True)

    table_rows = [
        [
            display_name_of(r["user"])[:14],
            human_hours(r["today"]),
            human_hours(r["week"]),
            human_hours(r["month"]),
            f"{r['efficiency']:.1f}",
            f"{r['streak']}🔥",
        ]
        for r in rows
    ]
    table = format_table(
        ["Member", "Today", "Week", "Month", "Eff", "Streak"], table_rows
    )

    missing = await reminder_service.missing_today(session)
    if missing:
        names = "\n".join(f"• {escape_html(display_name_of(u))}" for u in missing)
        missing_block = f"\n\n❌ <b>Missing Task List</b>\n{names}"
    else:
        missing_block = "\n\n✅ <b>Everyone logged today!</b>"

    total_today = sum(r["today"] for r in rows)
    lines = [
        bold("📊 Daily Study Report"),
        f"<i>{escape_html(today.isoformat())}</i>",
        f"Group total today: {bold(human_hours(total_today))}",
        "",
        table,
        missing_block,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Weekly report (run on Sunday)
# ---------------------------------------------------------------------------
async def build_weekly_report(session: AsyncSession) -> str:
    """Weekly summary: top performer, most improved, streaks, subjects..."""
    today = today_utc()
    week_start = start_of_week(today)
    prev_start = week_start - timedelta(days=7)
    prev_end = week_start - timedelta(days=1)

    this_week = await analytics_service.group_aggregates(session, week_start, today)
    last_week = await analytics_service.group_aggregates(session, prev_start, prev_end)
    users = {u.telegram_id: u for u in await _all_active_users(session)}

    if not this_week:
        return bold("📅 Weekly Report") + "\nNo study time logged this week. 🥲"

    # Top performer
    top_id = max(this_week, key=lambda k: this_week[k]["hours"])
    top_user = users.get(top_id)

    # Most improved (delta vs last week)
    improved_id, improved_delta = None, 0.0
    for tid, data in this_week.items():
        delta = data["hours"] - last_week.get(tid, {}).get("hours", 0.0)
        if delta > improved_delta:
            improved_delta, improved_id = delta, tid

    # Longest streak & most consistent
    longest_user = max(users.values(), key=lambda u: u.longest_streak, default=None)
    consistent_id = max(this_week, key=lambda k: this_week[k]["days"])
    consistent_user = users.get(consistent_id)

    subjects = await _subject_totals(session, week_start, today)
    weekly_total = sum(d["hours"] for d in this_week.values())
    weekly_avg = weekly_total / len(this_week) if this_week else 0.0

    subject_lines = "\n".join(
        f"  {escape_html(s)}: {human_hours(h)}" for s, h in list(subjects.items())[:8]
    ) or "  —"

    lines = [
        bold("📅 Weekly Report"),
        f"<i>{escape_html(week_start.isoformat())} → {escape_html(today.isoformat())}</i>",
        "",
        f"🏆 Top Performer: {bold(display_name_of(top_user))} "
        f"({human_hours(this_week[top_id]['hours'])})",
        f"📈 Most Improved: {bold(display_name_of(users.get(improved_id)))} "
        f"(+{human_hours(improved_delta)})",
        f"🔥 Longest Streak: {bold(display_name_of(longest_user))} "
        f"({longest_user.longest_streak} days)" if longest_user else "🔥 Longest Streak: —",
        f"🎯 Most Consistent: {bold(display_name_of(consistent_user))} "
        f"({this_week[consistent_id]['days']}/7 days)" if consistent_user else "🎯 Most Consistent: —",
        "",
        bold("📚 Subject Distribution"),
        subject_lines,
        "",
        f"🧮 Weekly Total: {bold(human_hours(weekly_total))}",
        f"📊 Weekly Average: {bold(human_hours(weekly_avg))}/member",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monthly report
# ---------------------------------------------------------------------------
async def build_monthly_report(session: AsyncSession) -> str:
    """Monthly deep-dive report."""
    today = today_utc()
    month_start = today.replace(day=1)
    days_so_far = (today - month_start).days + 1

    per_day = await _per_day_totals(session, month_start, today)
    aggregates = await analytics_service.group_aggregates(session, month_start, today)
    users = {u.telegram_id: u for u in await _all_active_users(session)}

    if not aggregates:
        return bold("🗓️ Monthly Report") + "\nNo study time logged this month yet."

    total_hours = sum(d["hours"] for d in aggregates.values())
    avg_daily = total_hours / days_so_far if days_so_far else 0.0
    best_day = max(per_day.items(), key=lambda kv: kv[1]) if per_day else None
    worst_day = min(per_day.items(), key=lambda kv: kv[1]) if per_day else None
    most_active_id = max(aggregates, key=lambda k: aggregates[k]["hours"])

    # Achievements awarded this month
    ach_result = await session.execute(
        select(Achievement).where(Achievement.achieved_at >= month_start)
    )
    achievements = list(ach_result.scalars().all())

    # Monthly leaderboard (top 5)
    board = await leaderboard_service.get_leaderboard(session, "monthly", use_cache=False)
    lb_rows = [
        [
            f"#{e.rank}",
            e.name[:14],
            human_hours(e.hours),
            f"Lv{e.level}",
        ]
        for e in board.entries[:5]
    ]
    lb_table = format_table(["Rank", "Member", "Hours", "Level"], lb_rows)

    lines = [
        bold("🗓️ Monthly Report"),
        f"<i>{escape_html(today.strftime('%B %Y'))}</i>",
        "",
        bold("🏅 Monthly Leaderboard"),
        lb_table,
        "",
        f"🧮 Total Hours: {bold(human_hours(total_hours))}",
        f"📊 Average Daily Hours: {bold(human_hours(avg_daily))}",
    ]
    if best_day:
        lines.append(
            f"🌟 Best Day: {bold(best_day[0].isoformat())} "
            f"({human_hours(best_day[1])})"
        )
    if worst_day:
        lines.append(
            f"📉 Quietest Day: {bold(worst_day[0].isoformat())} "
            f"({human_hours(worst_day[1])})"
        )
    lines.extend(
        [
            f"🥇 Most Active User: {bold(display_name_of(users.get(most_active_id)))}",
            f"💎 Longest Streak: "
            f"{bold(display_name_of(max(users.values(), key=lambda u: u.longest_streak, default=None)))}",
            f"🎖️ Achievements Awarded: {bold(str(len(achievements)))}",
        ]
    )
    return "\n".join(lines)


async def build_focus_summary(session: AsyncSession) -> str:
    """A short /focuscheck-friendly summary reused by the scheduler if needed."""
    data = await analytics_service.focus_check(session)
    return (
        bold("🔬 Group Focus Check")
        + f"\nAvg: {bold(human_hours(data['average_hours']))} • "
        f"Total: {bold(human_hours(data['total_hours']))}"
    )
