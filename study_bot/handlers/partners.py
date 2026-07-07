"""Handlers: /partner, /partners, /removepartner + partner notifications."""
from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, session_scope
from handlers.core import reply_html
from models import AccountabilityPartner
from services import reminder_service, user_service
from services.user_service import display_name_of
from utils.formatting import bold, escape_html
from utils.helpers import rate_limited
from utils.validation import ValidationError, parse_username

logger = logging.getLogger(__name__)


@rate_limited()
async def cmd_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set an accountability partner: /partner @username."""
    if not context.args:
        await reply_html(update, "Usage: /partner @username")
        return
    username = parse_username(context.args[0])
    if username is None:
        await reply_html(update, "❌ That doesn't look like a valid @username.")
        return

    user = update.effective_user
    try:
        async with session_scope() as session:
            await user_service.get_or_create_user(
                session, user.id, user.username, user.full_name
            )
            partner = await user_service.get_user_by_username(session, username)
            existing = await session.execute(
                select(AccountabilityPartner).where(
                    AccountabilityPartner.telegram_id == user.id,
                    AccountabilityPartner.partner_username == username,
                )
            )
            if existing.scalar_one_or_none() is not None:
                await reply_html(update, f"🤝 @{escape_html(username)} is already your partner.")
                return
            session.add(
                AccountabilityPartner(
                    telegram_id=user.id,
                    partner_username=username,
                    partner_telegram_id=partner.telegram_id if partner else None,
                )
            )
        await reply_html(
            update,
            f"🤝 <b>Accountability partner set:</b> @{escape_html(username)}\n"
            "They'll be notified if you miss a day of studying.",
        )
    except Exception:
        logger.exception("Error in /partner")
        raise


@rate_limited()
async def cmd_partners(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List accountability partners."""
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountabilityPartner).where(
                AccountabilityPartner.telegram_id == user.id
            )
        )
        partners = list(result.scalars().all())
    if not partners:
        await reply_html(update, "You have no accountability partners. Add one with /partner @username")
        return
    names = "\n".join(f"• @{escape_html(p.partner_username)}" for p in partners)
    await reply_html(update, f"🤝 <b>Your partners</b>\n{names}")


@rate_limited()
async def cmd_removepartner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove an accountability partner: /removepartner @username."""
    if not context.args:
        await reply_html(update, "Usage: /removepartner @username")
        return
    username = parse_username(context.args[0])
    if username is None:
        await reply_html(update, "❌ Invalid @username.")
        return
    user = update.effective_user
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(AccountabilityPartner).where(
                    AccountabilityPartner.telegram_id == user.id,
                    AccountabilityPartner.partner_username == username,
                )
            )
            partner = result.scalar_one_or_none()
            if partner is None:
                await reply_html(update, "No such partner found.")
                return
            await session.delete(partner)
        await reply_html(update, f"🗑️ Removed @{escape_html(username)} as a partner.")
    except Exception:
        logger.exception("Error in /removepartner")
        raise


async def notify_partners_of_missing(bot) -> None:
    """DM partners about members who skipped studying today.

    Called by the scheduler after building the daily report.
    """
    try:
        async with AsyncSessionLocal() as session:
            missing = await reminder_service.missing_today(session)
            if not missing:
                return
            missing_ids = {u.telegram_id for u in missing}
            result = await session.execute(select(AccountabilityPartner))
            partners = list(result.scalars().all())
            user_map = {u.telegram_id: u for u in missing}

        for partner_link in partners:
            target = partner_link.partner_telegram_id
            if target is None or partner_link.telegram_id not in missing_ids:
                continue
            member = user_map.get(partner_link.telegram_id)
            if member is None or bot is None:
                continue
            try:
                await bot.send_message(
                    chat_id=target,
                    text=(
                        f"🤝 Heads up! Your accountability partner "
                        f"<b>{escape_html(display_name_of(member))}</b> hasn't logged "
                        f"any study time today. Give them a nudge! 💪"
                    ),
                    parse_mode="HTML",
                )
            except Exception:  # pragma: no cover - user may have blocked the bot
                logger.warning("Could not notify partner %s", target)
    except Exception:
        logger.exception("notify_partners_of_missing failed")
