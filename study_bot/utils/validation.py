"""Input validation & parsing utilities.

Everything that comes from Telegram is treated as untrusted: hours are range
checked, subjects/note text is sanitised and the ``/log`` argument grammar is
parsed defensively.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from config import settings

# Telegram usernames start with @ and contain [A-Za-z0-9_]{5,32}
_USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{3,32})$")
# A finite decimal number (no scientific notation tricks)
_NUMBER_RE = re.compile(r"^\d{1,4}(\.\d{1,2})?$")

SUBJECT_MAX_LEN = 48
NOTE_MAX_LEN = 500


class ValidationError(ValueError):
    """Raised when user supplied input is invalid."""


@dataclass(frozen=True)
class ParsedLog:
    """The structured result of parsing a ``/log`` command."""

    subject: str
    hours: float
    note: str


def _is_number(token: str) -> bool:
    return bool(_NUMBER_RE.match(token))


def parse_log_command(args: Sequence[str]) -> ParsedLog:
    """Parse the arguments of ``/log``.

    Supported grammars::

        /log 3                          # hours only, subject = "General"
        /log Math 2                     # subject + hours
        /log Physics 1.5                # subject + fractional hours
        /log Math 3 Finished DP notes   # subject + hours + free note
        /log 2 dynamic programming      # hours + note (subject = "General")

    :raises ValidationError: when the grammar is ambiguous or invalid.
    """
    if not args:
        raise ValidationError(
            "Usage: /log &lt;hours&gt;  or  /log &lt;subject&gt; &lt;hours&gt; [note]"
        )

    first = args[0]
    if _is_number(first):
        # No explicit subject.
        hours = float(first)
        subject = "General"
        note = " ".join(args[1:]).strip()
    else:
        # First token is the subject; the second must be the hours.
        if len(args) < 2 or not _is_number(args[1]):
            raise ValidationError(
                "Couldn't find valid hours. Example: /log Math 2 or /log 3"
            )
        subject = first
        hours = float(args[1])
        note = " ".join(args[2:]).strip()

    subject = sanitise_subject(subject)
    note = sanitise_note(note)
    return ParsedLog(subject=subject, hours=hours, note=note)


def sanitise_subject(subject: str) -> str:
    """Normalise & length-limit a subject name."""
    cleaned = subject.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "General"
    if len(cleaned) > SUBJECT_MAX_LEN:
        cleaned = cleaned[:SUBJECT_MAX_LEN]
    return cleaned.title()


def sanitise_note(note: str) -> str:
    """Length-limit an optional note."""
    note = note.strip()
    if len(note) > NOTE_MAX_LEN:
        note = note[:NOTE_MAX_LEN]
    return note


def validate_hours(hours: float, daily_total: float = 0.0) -> None:
    """Range-check study hours.

    :raises ValidationError: for non-positive or implausibly large values.
    """
    max_hours = settings.max_hours_per_day
    if hours <= 0:
        raise ValidationError("Hours must be greater than zero.")
    if hours > max_hours:
        raise ValidationError(
            f"You can log at most {max_hours:g} hours in one go."
        )
    if daily_total + hours > max_hours:
        remaining = max(0.0, max_hours - daily_total)
        raise ValidationError(
            f"That would exceed {max_hours:g}h today "
            f"(only {remaining:g}h left for today)."
        )


def validate_goal_hours(hours: float) -> None:
    """Range-check a daily goal value."""
    if hours <= 0:
        raise ValidationError("Goal must be greater than zero.")
    if hours > 24:
        raise ValidationError("A daily goal cannot exceed 24 hours.")


def parse_username(raw: str) -> Optional[str]:
    """Return a clean username (without leading @) or ``None``."""
    if not raw:
        return None
    match = _USERNAME_RE.match(raw.strip())
    return match.group(1) if match else None


def parse_hours_token(token: str, field: str = "value") -> float:
    """Parse & validate a single numeric token (used by /goal, pomodoro...)."""
    if not _is_number(token):
        raise ValidationError(f"Invalid {field}: '{escape_safe(token)}'.")
    return float(token)


def escape_safe(text: str) -> str:
    """HTML-escape text intended for an error message."""
    from utils.formatting import escape_html

    return escape_html(text)
