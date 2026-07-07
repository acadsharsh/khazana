"""Handlers: /subjects, /show<subject>, /calendar, /focuscheck."""
from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from config import get_focus_threshold
from database import AsyncSessionLocal
from handlers.core import reply_html
from services import analytics_service, user_service
from utils.formatting import bold, escape_html, format_table, human_hours
from utils.helpers import rate_limited
from utils.validation import sanitise_subject

logger = logging.getLogger(__name__)


@rate_limited()
async def cmd_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hours grouped by subject."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        subjects = await analytics_service.subject_breakdown(session, user.id)
        total = await user_service.get_user(session, user.id)

    if not subjects:
        await reply_html(update, "📚 No subjects logged yet. Try /log Math 2")
        return

    rows = [
        [escape_html(s), human_hours(h), f"{h / sum(subjects.values()) * 100:.0f}%"]
        for s, h in subjects.items()
    ]
    table = format_table(["Subject", "Hours", "Share"], rows)
    lifetime = human_hours(total.total_study_hours) if total else "—"
    await reply_html(
        update,
        f"📚 <b>Subjects — {escape_html(update.effective_user.full_name)}</b>\n"
        f"Lifetime: {bold(lifetime)}\n\n{table}",
    )


@rate_limited()
async def cmd_show_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show stats for a single subject.

    Supports ``/showsubject Math`` and ``/showmath`` style aliases.
    """
    text = update.message.text or ""
    subject = None
    if context.args:
        subject = " ".join(context.args)
    else:
        match = re.match(r"^/show\s*(.+)$", text)
        if match:
            subject = match.group(1)
    subject = sanitise_subject(subject or "General")

    user = update.effective_user
    async with AsyncSessionLocal() as session:
        breakdown = await analytics_service.subject_breakdown(session, user.id)
        history = await analytics_service.subject_history(session, user.id, subject, 30)

    total = breakdown.get(subject, 0.0)
    days_logged = len(history)
    last_30 = sum(history.values())
    bars = "▁▂▃▄▅▆▇█"
    spark = "".join(
        bars[min(int((h / (max(history.values()) or 1)) * (len(bars) - 1)), len(bars) - 1)]
        for h in history.values()
    ) if history else ""

    await reply_html(
        update,
        f"📖 <b>{escape_html(subject)}</b>\n"
        f"Lifetime: {bold(human_hours(total))}\n"
        f"Last 30 days: {bold(human_hours(last_30))} across {days_logged} days\n"
        f"<code>{spark}</code>",
    )


@rate_limited()
async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """30-day study calendar grid."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        calendar = await analytics_service.study_calendar(session, user.id, 30)
    icons = []
    for day, hours in calendar:
        if day == calendar[-1][0]:
            icons.append("🟦" if hours <= 0 else "🟩")
        else:
            icons.append("🟩" if hours > 0 else "🟥")
    weeks = ["\n".join("".join(icons[i : i + 7])) for i in range(0, len(icons), 7)]
    grid = "\n\n".join(["".join(icons[i : i + 7]) for i in range(0, len(icons), 7)])
    await reply_html(
        update,
        f"🗓️ <b>Study Calendar (30 days)</b>\n"
        f"🟩 Logged  🟥 Missed  🟦 Today\n<pre>{grid}</pre>",
    )


@rate_limited()
async def cmd_focuscheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group focus summary with motivational or celebratory message."""
    async with AsyncSessionLocal() as session:
        data = await analytics_service.focus_check(session)

    avg = data["average_hours"]
    threshold = get_focus_threshold()
    below = avg < threshold
    table = format_table(
        ["Metric", "Value"],
        [
            ["Group average", human_hours(avg)],
            ["Total today", human_hours(data["total_hours"])],
            ["Active users", str(data["active_users"])],
            ["Inactive users", str(data["inactive_users"])],
            ["Total members", str(data["total_members"])],
        ],
    )

    if below:
        message = (
            "💪 <b>Let's pick it up, team!</b> The group average is below our "
            f"goal of {threshold:g}h. Log a focused session now - "
            "every minute counts and your streak is on the line! 🚀"
        )
    else:
        message = (
            "🎉 <b>Outstanding focus, team!</b> The group is crushing it today. "
            "Keep the momentum going - consistency is the secret weapon. 🔥"
        )
    await reply_html(update, f"🔬 <b>Focus Check</b>\n\n{table}\n\n{message}")
