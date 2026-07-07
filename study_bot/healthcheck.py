"""Container health check.

Verifies the SQLite database exists and its schema is queryable. Used by the
Docker ``HEALTHCHECK`` directive.
"""
from __future__ import annotations

import os
import sqlite3
import sys


def main() -> int:
    db_path = os.path.join("data", "study_bot.db")
    if not os.path.exists(db_path):
        print("database file missing")
        return 1
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1 FROM users LIMIT 1")
        conn.close()
    except sqlite3.Error as exc:  # pragma: no cover
        print(f"database unhealthy: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
