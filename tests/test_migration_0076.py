"""The migration 0076 (the torrent editor places and never leaves out; a folder of subtitles has no type «forced»), proved
before it is run on production; the test goes with the commit that runs it there."""
import importlib.util
import sqlite3
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent
BEFORE = ("""CREATE TABLE unpack_assignments (url TEXT NOT NULL, tab TEXT NOT NULL CHECK (tab IN ('files', 'voices', 'subtitles', 'streams')),
            item TEXT NOT NULL, season INTEGER, episode INTEGER, episode_last INTEGER, folder TEXT,
            excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)), PRIMARY KEY (url, tab, item),
            CHECK (excluded = 1 OR (tab = 'files' AND season IS NOT NULL AND episode IS NOT NULL AND folder IS NULL
                   AND (episode_last IS NULL OR episode_last > episode))
                   OR (tab <> 'files' AND folder IS NOT NULL AND season IS NULL AND episode IS NULL AND episode_last IS NULL)))""",
          """CREATE TABLE unpack_folders (url TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('voice', 'subtitles')),
            name TEXT NOT NULL CHECK (name <> ''), language TEXT NOT NULL DEFAULT '',
            subtitle_type TEXT NOT NULL DEFAULT '' CHECK (subtitle_type IN ('', 'full', 'signs', 'forced')),
            PRIMARY KEY (url, kind, name))""")


def run_upgrade(path: str) -> None:
    spec = importlib.util.spec_from_file_location("m0076", ROOT / "alembic" / "versions" / "0076_unpack_no_exclusion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def test_the_hands_never_leaves_out_its_placements_stay_and_a_folder_forced_is_of_no_type(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("DROP TABLE unpack_assignments")
    db.execute("DROP TABLE unpack_folders")
    db.execute("DROP TABLE unpack_hidden")
    for sql in BEFORE:
        db.execute(sql)
    rows = [("u", "files", "E01.mkv", 1, 5, None, None, 0), ("u", "files", "E02.mkv", None, None, None, None, 1),
            ("u", "streams", "E02.mkv#4", None, None, None, None, 1), ("u", "streams", "E04.mkv#4", None, None, None, "Original", 0)]
    db.executemany("INSERT INTO unpack_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    db.executemany("INSERT INTO unpack_folders VALUES (?, ?, ?, ?, ?)",
                   [("u", "subtitles", "Надписи", "rus", "signs"), ("u", "subtitles", "Форс", "", "forced")])
    db.commit()
    db.close()

    run_upgrade(path)

    db = sqlite3.connect(path)
    assert db.execute("SELECT url, tab, item, season, episode, episode_last, folder FROM unpack_assignments ORDER BY item").fetchall() == [
        ("u", "files", "E01.mkv", 1, 5, None, None), ("u", "streams", "E04.mkv#4", None, None, None, "Original")]
    assert "excluded" not in [row[1] for row in db.execute("PRAGMA table_info(unpack_assignments)")]
    assert db.execute("SELECT name, language, subtitle_type FROM unpack_folders ORDER BY name").fetchall() == [
        ("Надписи", "rus", "signs"), ("Форс", "", "")]
    assert db.execute("SELECT count(*) FROM unpack_hidden").fetchone() == (0,)
