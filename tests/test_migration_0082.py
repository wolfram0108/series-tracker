"""The migration 0082 (the schedule by the time of day, the terms by the site, the time a release was checked), proved before
it is run on production; the test goes with the commit that runs it there."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("m0082", ROOT / "alembic" / "versions" / "0082_schedule_by_time_of_day.py")
m0082 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m0082)


def migrate(path):
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            m0082.upgrade()


def test_an_interval_becomes_the_times_of_day_from_the_next_sync_it_had_set():
    local = datetime(2026, 11, 18, 16, 40).astimezone()
    assert m0082.sync_times(720, local.astimezone(timezone.utc).isoformat(), local) == ["04:40", "16:40"]
    assert m0082.sync_times(360, local.isoformat(), local) == ["04:40", "10:40", "16:40", "22:40"]
    # An interval shorter than half an hour is the 48 points of the day at most.
    assert len(m0082.sync_times(5, local.isoformat(), local)) == 48
    assert m0082.sync_times(1440, None, local) == ["16:40"]


def test_the_settings_are_moved_and_a_release_gets_checked_at(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE torrent_releases DROP COLUMN checked_at")
    db.execute("DELETE FROM settings")
    next_scan = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    db.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", [
        ("scan_interval_minutes", "720"), ("next_scan_timestamp", next_scan), ("sources_term_hours", "6")])
    db.executemany("INSERT INTO series_sources (series_id, url, source_type) VALUES (1, ?, ?)", [
        ("https://old.yummyani.me/catalog/item/a", "web_video"), ("app://banri/1", "web_video"),
        ("https://vkvideo.ru/@chan|q", "vk_video"), ("https://anilibria.top/x", "torrent")])
    db.commit()
    db.close()
    migrate(path)
    db = sqlite3.connect(path)
    values = dict(db.execute("SELECT key, value FROM settings").fetchall())
    columns = [row[1] for row in db.execute("PRAGMA table_info(torrent_releases)")]
    local = datetime.fromisoformat(next_scan).astimezone()
    schedule = json.loads(values["schedule"])
    assert f"{local.hour:02d}:{local.minute:02d}" in schedule["syncs"] and len(schedule["syncs"]) == 2
    assert schedule["torrent_mode"] == "between" and schedule["torrent_between"] == 1
    assert json.loads(values["sources.terms"]) == {"banri": 360, "old.yummyani.me": 360, "vkvideo.ru": 360}
    assert "scan_interval_minutes" not in values and "sources_term_hours" not in values
    assert "checked_at" in columns


def test_the_default_term_and_a_new_install_write_nothing_of_their_own(tmp_path):
    """An hour is the term of a site the setting does not name: a term of an hour writes no term of a site."""
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE torrent_releases DROP COLUMN checked_at")
    db.execute("DELETE FROM settings")
    db.execute("INSERT INTO settings (key, value) VALUES ('sources_term_hours', '1')")
    db.commit()
    db.close()
    migrate(path)
    values = dict(sqlite3.connect(path).execute("SELECT key, value FROM settings").fetchall())
    assert values == {}
