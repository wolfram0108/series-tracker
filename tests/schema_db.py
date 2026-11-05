"""The database of a test: the schema of the migrations, built once per process and copied for each test.

Building the schema runs every migration (about a second); a copy of the built file takes a millisecond. A test that
needs the head of the schema calls `migrated(path)`; the migrations themselves are proved once, on the way to the
production database, not again in every test."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_template: str | None = None


def _built() -> str:
    """The schema at the head of the migrations, built by the migrations themselves the first time a test asks."""
    global _template
    if _template is None:
        folder = tempfile.mkdtemp(prefix="st-schema-")
        atexit.register(shutil.rmtree, folder, True)
        path = os.path.join(folder, "head.db")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=ROOT, check=True,
                       capture_output=True)
        _template = path
    return _template


def migrated(path: str | os.PathLike) -> str:
    """A database file at `path` with the schema of the migrations and no data."""
    shutil.copyfile(_built(), path)
    return str(path)
