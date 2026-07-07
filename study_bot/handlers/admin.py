"""Admin-only handlers: stats, export, broadcast, user management, backup."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

import config
from config import settings
from database import AsyncSessionLocal, engine, init_db, session_scope
from handlers.core import reply_html
from models import Achievement, StudyLog, User
from services import analytics_service, user_service
from utils.formatting import bold, escape_html, format_table, human_hours
from utils.helpers import admin_only, rate_limited
from utils.validation import ValidationError, parse_hours_token

logger = logging.getLogger(__name__)


def _db_file_path() -> str:
    """Return the on-disk sqlite file path (empty string if not file based)."""
    url = settings.database_url
    if "sqlite" not in url or ":memory:" in url:
        return ""
    return url.split("///")[-1]


@admin_only
@rate_limited()
async def cmd_adminstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group-wide administrative statistics."""
    async with AsyncSessionLocal() as session:
        users = await user_service.list_users(session, active_only=False)
        active = [u for u in users if u.is_active]
        total_hours = sum(u.total_study_hours for u in users)
        focus = await analytics_service.focus_check(session)
        ach_result = await session.execute(select(func.count(Achievement.id)))
        ach_count = int(ach_result.scalar_one())

    table = format_table(
        ["Metric", "Value"],
        [
            ["Total members", str(len(users))],
            ["Active members", str(len(active))],
            ["Total study hours", human_hours(total_hours)],
            ["Studied today", str(focus["active_users"])],
            ["Inactive today", str(focus["inactive_users"])],
            ["Today's total", human_hours(focus["total_hours"])],
            ["Today's average", human_hours(focus["average_hours"])],
            ["Achievements", str(ach_count)],
            ["Focus threshold", f"{config.get_focus_threshold():g}h"],
        ],
    )
    await reply_html(update, f"🛠️ <b>Admin Dashboard</b>\n\n{table}")


@admin_only
@rate_limited()
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export study logs: /export csv|json|excel."""
    fmt = (context.args[0].lower() if context.args else "csv")
    if fmt not in {"csv", "json", "excel"}:
        await reply_html(update, "Usage: /export csv|json|excel")
        return
    from exports.exporters import export_data

    try:
        async with AsyncSessionLocal() as session:
            path = await export_data(session, fmt)
        with open(path, "rb") as fh:
            await update.effective_message.reply_document(
                document=fh,
                filename=os.path.basename(path),
                caption=f"📤 Export ({fmt}): {os.path.basename(path)}",
            )
    except Exception:
        logger.exception("Export failed")
        await reply_html(update, "❌ Export failed. Check the logs.")


@admin_only
@rate_limited()
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to the configured group: /broadcast <text>."""
    if not context.args:
        await reply_html(update, "Usage: /broadcast &lt;message&gt;")
        return
    message = escape_html(" ".join(context.args))
    if not settings.group_id:
        await reply_html(update, "❌ GROUP_ID is not configured.")
        return
    try:
        await context.bot.send_message(
            chat_id=settings.group_id,
            text=f"📢 <b>Announcement</b>\n\n{message}",
            parse_mode="HTML",
        )
        await reply_html(update, "✅ Broadcast sent to the group.")
    except Exception:
        logger.exception("Broadcast failed")
        await reply_html(update, "❌ Broadcast failed.")


@admin_only
@rate_limited()
async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all registered users."""
    async with AsyncSessionLocal() as session:
        users = await user_service.list_users(session, active_only=False)
    if not users:
        await reply_html(update, "No users registered yet.")
        return
    rows = [
        [
            ("🟢" if u.is_active else "🔴"),
            escape_html((u.display_name or str(u.telegram_id))[:14]),
            str(u.telegram_id),
            human_hours(u.total_study_hours),
            f"Lv{u.level}",
        ]
        for u in users[:25]
    ]
    table = format_table(["", "Name", "Telegram ID", "Hours", "Lvl"], rows)
    await reply_html(update, f"👥 <b>Registered Users ({len(users)})</b>\n\n{table}")


@admin_only
@rate_limited()
async def cmd_resetuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset a user's progress: /resetuser <telegram_id>."""
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await reply_html(update, "Usage: /resetuser &lt;telegram_id&gt;")
        return
    target_id = int(context.args[0])
    async with session_scope() as session:
        user = await user_service.reset_progress(session, target_id)
    if user is None:
        await reply_html(update, "❌ User not found.")
        return
    await reply_html(update, f"♻️ Reset progress for <b>{escape_html(user.display_name)}</b>.")


@admin_only
@rate_limited()
async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deactivate a user: /removeuser <telegram_id>."""
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await reply_html(update, "Usage: /removeuser &lt;telegram_id&gt;")
        return
    target_id = int(context.args[0])
    async with session_scope() as session:
        ok = await user_service.deactivate(session, target_id)
    if not ok:
        await reply_html(update, "❌ User not found.")
        return
    await reply_html(update, f"🗑️ Deactivated user <code>{target_id}</code>.")


@admin_only
@rate_limited()
async def cmd_setthreshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update the focus threshold at runtime: /setthreshold 2.5."""
    if not context.args:
        await reply_html(update, "Usage: /setthreshold &lt;hours&gt;")
        return
    try:
        value = parse_hours_token(context.args[0], field="threshold")
    except ValidationError as exc:
        await reply_html(update, f"❌ {exc}")
        return
    config.set_runtime("focus_threshold", value)
    async with session_scope() as session:
        from models import Setting

        result = await session.execute(select(Setting).where(Setting.key == "focus_threshold"))
        setting = result.scalar_one_or_none()
        payload = str(value)
        if setting is None:
            session.add(Setting(key="focus_threshold", value=payload))
        else:
            setting.value = payload
    await reply_html(update, f"✅ Focus threshold set to <b>{value:g}h</b>.")


@admin_only
@rate_limited()
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Copy the database file and send it as a document."""
    src = _db_file_path()
    if not src or not os.path.exists(src):
        await reply_html(update, "❌ Backup is only supported for file-based SQLite databases.")
        return
    os.makedirs("data/exports", exist_ok=True)
    dest = os.path.abspath(
        os.path.join("data/exports", f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db")
    )
    try:
        shutil.copy2(src, dest)
        with open(dest, "rb") as fh:
            await update.effective_message.reply_document(
                document=fh,
                filename=os.path.basename(dest),
                caption=f"💾 Database backup: {os.path.basename(dest)}",
            )
    except Exception:
        logger.exception("Backup failed")
        await reply_html(update, "❌ Backup failed.")


@admin_only
@rate_limited()
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore the database from the latest backup file."""
    src = _db_file_path()
    export_dir = os.path.abspath("data/exports")
    if not src:
        await reply_html(update, "❌ Restore is only supported for file-based SQLite databases.")
        return
    backups = sorted(
        f for f in os.listdir(export_dir) if f.startswith("backup_") and f.endswith(".db")
    ) if os.path.isdir(export_dir) else []
    if not backups:
        await reply_html(update, "❌ No backup files found in data/exports.")
        return
    latest = os.path.join(export_dir, backups[-1])
    try:
        # dispose engine connections before overwriting the file
        await engine.dispose()
        shutil.copy2(latest, src)
        await init_db()
        await reply_html(update, f"♻️ Restored database from <b>{backups[-1]}</b>.")
    except Exception:
        logger.exception("Restore failed")
        await reply_html(update, "❌ Restore failed.")
