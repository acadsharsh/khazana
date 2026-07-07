"""Output formatting helpers (HTML escaping, progress bars, tables).

All user-facing text is built with Telegram HTML and every piece of
untrusted input is escaped with :func:`html.escape` to prevent injection.
"""
from __future__ import annotations

import html
from typing import Iterable, List, Sequence


# ---------------------------------------------------------------------------
# Escaping & inline formatting
# ---------------------------------------------------------------------------
def escape_html(text) -> str:
    """HTML-escape arbitrary text (``None`` becomes an empty string)."""
    if text is None:
        return ""
    return html.escape(str(text), quote=False)


def bold(text) -> str:
    return f"<b>{escape_html(text)}</b>"


def italic(text) -> str:
    return f"<i>{escape_html(text)}</i>"


def code(text) -> str:
    return f"<code>{escape_html(text)}</code>"


def pre(text) -> str:
    return f"<pre>{escape_html(text)}</pre>"


def link(text, url: str) -> str:
    return f'<a href="{escape_html(url)}">{escape_html(text)}</a>'


def emoji_badge(level: int) -> str:
    """A small emoji representing a level tier."""
    if level >= 30:
        return "🦁"
    if level >= 20:
        return "🦅"
    if level >= 10:
        return "🐺"
    if level >= 5:
        return "⭐"
    return "🌱"


# ---------------------------------------------------------------------------
# Progress bars
# ---------------------------------------------------------------------------
def progress_bar(current: float, goal: float, width: int = 10) -> str:
    """Render a textual progress bar such as ``█████░░░░░``."""
    if goal <= 0:
        return "░" * width
    ratio = max(0.0, min(current / goal, 1.0))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def percent_bar(value: float, width: int = 10) -> str:
    """Render a 0-100 percentage as a progress bar."""
    return progress_bar(value, 100.0, width)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def format_table(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    """Render an aligned monospace table inside a ``<pre>`` block.

    Example::

        >>> format_table(["A", "B"], [["1", "2"], ["30", "4"]])
        '<pre>A  B\n1  2\n30 4</pre>'
    """
    header = [str(h) for h in headers]
    str_rows = [[str(c) for c in row] for row in rows]
    all_rows = [header] + str_rows
    if not all_rows or not all_rows[0]:
        return pre("")
    n_cols = len(all_rows[0])
    widths = [
        max(len(r[i]) for r in all_rows if i < len(r)) for i in range(n_cols)
    ]
    lines: List[str] = []
    for r in all_rows:
        line = "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(r)))
        lines.append(line.rstrip())
    # separate header with a thin underline
    if len(lines) >= 1:
        sep = "  ".join("-" * widths[i] for i in range(n_cols))
        lines.insert(1, sep)
    return pre("\n".join(lines))


def hr(char: str = "─", width: int = 28) -> str:
    """A thin horizontal rule (escaped)."""
    return escape_html(char * width)


# ---------------------------------------------------------------------------
# Misc display helpers
# ---------------------------------------------------------------------------
def human_hours(value: float) -> str:
    """Render hours compactly: ``3`` -> ``3h``, ``1.5`` -> ``1.5h``."""
    if value == int(value):
        return f"{int(value)}h"
    return f"{value:g}h"


def movement_arrow(direction: str) -> str:
    """Map a movement token to an arrow emoji."""
    return {"up": "🔺", "down": "🔻", "same": "➖"}.get(direction, "➖")


def truncate(text: str, limit: int = 3500) -> str:
    """Cap very long messages inside Telegram's 4096 char limit."""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…(trunc)"
