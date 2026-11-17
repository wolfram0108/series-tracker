"""The migration 0077 (the tab «Контейнеры» becomes «Озв. конт.» and «Суб. конт.»), proved before it is run on production; the
test goes with the commit that runs it there."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def run(path: str, name: str) -> None:
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade() if name == "undo" else module.upgrade()


def test_a_placement_in_a_container_follows_the_kind_of_its_stream_and_the_old_profile_goes_to_the_sound(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    # The store as 0076 left it: one tab of the containers, one profile for it.
    db.execute("DROP TABLE unpack_assignments")
    db.execute("""CREATE TABLE unpack_assignments (url TEXT NOT NULL, tab TEXT NOT NULL, item TEXT NOT NULL, season INTEGER,
                  episode INTEGER, episode_last INTEGER, folder TEXT, PRIMARY KEY (url, tab, item))""")
    db.execute("ALTER TABLE unpack_sources RENAME COLUMN voice_streams_profile_id TO streams_profile_id")
    db.execute("ALTER TABLE unpack_sources DROP COLUMN subtitle_streams_profile_id")
    db.execute("INSERT INTO parser_profiles (id, name, kind) VALUES (7, 'Торрент: u · контейнеры', 'torrent')")
    db.execute("INSERT INTO unpack_sources (url, streams_profile_id) VALUES ('u', 7)")
    db.execute("INSERT INTO unpack_streams (url, release, path, infohash, size, streams, attachments, chapters) "
               "VALUES ('u', 'r/x', 'S/E01.mkv', 'h', 1, ?, '[]', 0)",
               (json.dumps([{"index": 0, "kind": "video"}, {"index": 1, "kind": "audio"}, {"index": 2, "kind": "subtitle"}]),))
    db.executemany("INSERT INTO unpack_assignments VALUES ('u', ?, ?, NULL, NULL, NULL, ?)",
                   [("streams", "r/x/S/E01.mkv#1", "Студия"), ("streams", "r/x/S/E01.mkv#2", "Надписи"),
                    ("voices", "r/x/S/E01.mka", "Студия")])
    db.commit()
    db.close()

    run(path, "0077_unpack_container_tabs")

    db = sqlite3.connect(path)
    assert db.execute("SELECT tab, item, folder FROM unpack_assignments ORDER BY item").fetchall() == [
        ("voices", "r/x/S/E01.mka", "Студия"), ("voice_streams", "r/x/S/E01.mkv#1", "Студия"),
        ("subtitle_streams", "r/x/S/E01.mkv#2", "Надписи")]
    assert db.execute("SELECT voice_streams_profile_id, subtitle_streams_profile_id FROM unpack_sources").fetchall() == [(7, None)]
    assert db.execute("SELECT name FROM parser_profiles WHERE id = 7").fetchone() == ("Торрент: u · озв. конт.",)
