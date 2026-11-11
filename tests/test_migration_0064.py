"""The migration 0064 (a row of a player switched off in the settings is excluded as `settings`), proved before it is run
on production; the test goes with the commit that runs it there."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def migration():
    spec = importlib.util.spec_from_file_location("m0064", ROOT / "alembic" / "versions" / "0064_exclusion_settings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(db):
    return db.execute("SELECT * FROM composition_rows ORDER BY seq").fetchall()


def test_the_table_built_again_keeps_every_column_of_every_row_and_takes_the_new_reason(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, excluded, exclusion, season, episode_start,"
               " player, state, heights, video, stale) VALUES (40, 59, 'k1', 1, 1, 'source', 1, 3, 'kodik', 'playable',"
               " '[720]', 'v', 1)")
    db.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, excluded, exclusion, player, state)"
               " VALUES (40, 59, 'k2', 2, 1, 'settings', 'alloha', 'playable')")
    db.commit()
    before, schema = rows(db), db.execute("PRAGMA table_info(composition_rows)").fetchall()
    m = migration()

    with create_engine(f"sqlite:///{path}").begin() as bind:
        m.rebuild(bind, m._AFTER, m._BEFORE, "exclusion IS NULL OR exclusion != 'settings'")
    assert rows(db) == before[:1]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, excluded, exclusion, player, state)"
                   " VALUES (40, 59, 'k3', 3, 1, 'settings', 'alloha', 'playable')")
    db.rollback()

    with create_engine(f"sqlite:///{path}").begin() as bind:
        m.rebuild(bind, m._BEFORE, m._AFTER)
    assert rows(db) == before[:1]
    assert db.execute("PRAGMA table_info(composition_rows)").fetchall() == schema
    db.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, excluded, exclusion, player, state)"
               " VALUES (40, 59, 'k2', 2, 1, 'settings', 'alloha', 'playable')")
