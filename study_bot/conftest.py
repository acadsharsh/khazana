"""Root conftest: ensure the project root is importable and tests use an
isolated SQLite database (never the platform's external DATABASE_URL).
"""
import os
import sys
import tempfile

# Make `import config`, `import models`, ... resolve to this project.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force a throwaway SQLite file so importing database.py never tries to reach
# an external Postgres instance configured by the sandbox.
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///" + os.path.join(tempfile.gettempdir(), "study_bot_test.db"),
)
