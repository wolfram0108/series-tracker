"""The migration 0080 (the tag of a video placed by hand), proved before it is run on production; the test goes with the commit
that runs it there."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def test_the_placements_kept_stay_with_no_tag_and_a_track_takes_none(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("DROP TABLE unpack_assignments")
    db.execute("CREATE TABLE unpack_assignments (url TEXT, tab TEXT, item TEXT, season INTEGER, episode INTEGER, "
               "episode_last INTEGER, folder TEXT, PRIMARY KEY (url, tab, item))")
    db.executemany("INSERT INTO unpack_assignments VALUES ('u', ?, ?, ?, ?, ?, ?)",
                   [("files", "r/a.mkv", 1, 21, None, None), ("files", "r/b.mkv", 1, 3, 4, None),
                    ("voices", "r/a.mka", None, None, None, "Студия")])
    db.commit()
    db.close()
    spec = importlib.util.spec_from_file_location("m0080", ROOT / "alembic" / "versions" / "0080_unpack_video_tag.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
    db = sqlite3.connect(path)
    assert db.execute("SELECT tab, item, season, episode, episode_last, folder, tag FROM unpack_assignments ORDER BY item").fetchall() == [
        ("voices", "r/a.mka", None, None, None, "Студия", ""), ("files", "r/a.mkv", 1, 21, None, None, ""),
        ("files", "r/b.mkv", 1, 3, 4, None, "")]
    db.execute("INSERT INTO unpack_assignments (url, tab, item, season, episode, tag) VALUES ('u', 'files', 'r/c.mkv', 1, 21, 'DIR')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO unpack_assignments (url, tab, item, folder, tag) VALUES ('u', 'voices', 'r/c.mka', 'Студия', 'DIR')")
