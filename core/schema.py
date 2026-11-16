"""The schema of the database against the code: the service starts only on a database the migrations of its code reached.

The service runs from the checkout its updates are pulled into, so a restart takes the code as it is; a database behind it
(an update whose `alembic upgrade head` did not run yet) would serve some windows and break others. The start refuses such
a database at once, saying which revision it holds, which the code needs and what to run (docs/deployment.md, section 3).
"""
from __future__ import annotations

import os
import sqlite3

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SchemaBehind(RuntimeError):
    """The database holds an older revision than the code needs."""


def head_revision() -> str:
    """The last migration of the code."""
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def database_revision(path: str) -> str | None:
    """The revision the database holds; None for a database the migrations never touched (or no file at all)."""
    if not os.path.exists(path):
        return None
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None


def require_head(path: str) -> None:
    """Raise SchemaBehind unless the database at `path` holds the last migration of the code."""
    held, needed = database_revision(path), head_revision()
    if held != needed:
        raise SchemaBehind(f"база {path} на ревизии {held or 'без миграций'}, код требует {needed}: сначала бэкап и "
                           f"`.venv/bin/alembic upgrade head` (docs/deployment.md, раздел 3), затем запуск")
