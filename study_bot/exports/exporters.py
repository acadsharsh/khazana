"""Export utilities: serialise study logs and users to CSV / JSON / Excel.

All exports are written into ``data/exports/`` and the absolute path is
returned so the caller can attach the file to a Telegram message.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import StudyLog, User
from config import settings

logger = logging.getLogger(__name__)

EXPORT_DIR = os.path.join("data", "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


async def _fetch_rows(session: AsyncSession) -> List[dict]:
    """Collect a flat list of log rows joined with user display info."""
    users_result = await session.execute(select(User))
    users = {u.telegram_id: u for u in users_result.scalars().all()}

    logs_result = await session.execute(
        select(StudyLog).order_by(StudyLog.timestamp.desc())
    )
    rows: List[dict] = []
    for log in logs_result.scalars().all():
        user = users.get(log.telegram_id)
        rows.append(
            {
                "log_id": log.id,
                "telegram_id": log.telegram_id,
                "username": log.username or "",
                "name": (user.display_name if user else ""),
                "subject": log.subject,
                "hours": log.hours,
                "note": log.note or "",
                "date": log.log_date.isoformat(),
                "week_number": log.week_number,
                "month": log.month,
                "year": log.year,
                "xp_earned": log.xp_earned,
                "timestamp": log.timestamp.isoformat() if log.timestamp else "",
            }
        )
    return rows


def _timestamped(name: str, ext: str) -> str:
    filename = f"{name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.abspath(os.path.join(EXPORT_DIR, filename))


async def export_csv(session: AsyncSession) -> str:
    """Export all study logs to a CSV file."""
    rows = await _fetch_rows(session)
    path = _timestamped("study_logs", "csv")
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("No study logs found.\n")
    logger.info("Exported CSV to %s", path)
    return path


async def export_json(session: AsyncSession) -> str:
    """Export all study logs to a JSON file."""
    rows = await _fetch_rows(session)
    path = _timestamped("study_logs", "json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False, default=str)
    logger.info("Exported JSON to %s", path)
    return path


async def export_excel(session: AsyncSession) -> str:
    """Export all study logs to an .xlsx workbook (falls back to CSV)."""
    try:
        from openpyxl import Workbook
    except ImportError:  # pragma: no cover - openpyxl is in requirements
        logger.warning("openpyxl not installed; falling back to CSV export.")
        return await export_csv(session)

    rows = await _fetch_rows(session)
    path = _timestamped("study_logs", "xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Study Logs"
    if rows:
        ws.append(list(rows[0].keys()))
        for row in rows:
            ws.append(list(row.values()))
    else:
        ws.append(["No study logs found."])
    wb.save(path)
    logger.info("Exported XLSX to %s", path)
    return path


EXPORTERS = {"csv": export_csv, "json": export_json, "excel": export_excel}


async def export_data(session: AsyncSession, fmt: str = "csv") -> str:
    """Dispatch helper used by the admin /export command."""
    exporter = EXPORTERS.get(fmt.lower(), export_csv)
    return await exporter(session)
