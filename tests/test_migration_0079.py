"""The migration 0079 (a folder of the torrent editor belongs to its tab), proved before it is run on production; the test
goes with the commit that runs it there."""
import importlib.util
import sqlite3
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def test_a_folder_kept_by_its_kind_goes_to_both_tabs_of_the_kind_with_its_settings(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    for table, columns in (("unpack_folders", "url TEXT, kind TEXT, name TEXT, language TEXT, subtitle_type TEXT"),
                           ("unpack_hidden", "url TEXT, kind TEXT, name TEXT")):
        db.execute(f"DROP TABLE {table}")
        db.execute(f"CREATE TABLE {table} ({columns})")
    db.executemany("INSERT INTO unpack_folders VALUES ('u', ?, ?, ?, ?)",
                   [("voice", "Original", "jpn", ""), ("subtitles", "Надписи", "rus", "signs")])
    db.execute("INSERT INTO unpack_hidden VALUES ('u', 'voice', 'Студия')")
    db.commit()
    db.close()
    spec = importlib.util.spec_from_file_location("m0079", ROOT / "alembic" / "versions" / "0079_unpack_folders_by_tab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
    db = sqlite3.connect(path)
    assert db.execute("SELECT tab, name, language, subtitle_type FROM unpack_folders ORDER BY tab, name").fetchall() == [
        ("subtitle_streams", "Надписи", "rus", "signs"), ("subtitles", "Надписи", "rus", "signs"),
        ("voice_streams", "Original", "jpn", ""), ("voices", "Original", "jpn", "")]
    assert db.execute("SELECT tab, name FROM unpack_hidden ORDER BY tab").fetchall() == [("voice_streams", "Студия"), ("voices", "Студия")]
