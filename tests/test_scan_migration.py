"""Migration 0028: plan rows refer to composition rows and the references are kept with their marks, proved on data.

The database is brought to revision 0027 and filled with a series, rows of its plan, a report of players and a journal
task, then migrated up and back down by the real alembic. What is checked is what the owner would lose: every row of
the plan keeps its id, its file and its status, the report of players goes, and the new table keeps one record per slot.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from series_factory import insert_series

ROOT = Path(__file__).resolve().parent.parent
PLAN_ROWS = [("u1", 1, 1, "completed", "e01.mp4", "in_plan_single"), ("u2", 1, 2, "pending", None, "candidate"),
             ("u3", 2, 7, "error", None, "replaced")]


def alembic(db_path, *args):
    done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, capture_output=True, text=True,
                          env={"ST_DB_URL": f"sqlite:///{db_path}", "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr


def query(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]


def names(db_path, kind):
    return {row["name"] for row in query(db_path, f"SELECT name FROM sqlite_master WHERE type='{kind}'")}


def columns(db_path, table):
    return [row["name"] for row in query(db_path, f"PRAGMA table_info({table})")]


@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("migration") / "old.db"
    alembic(path, "upgrade", "0027")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'профиль')")
        insert_series(conn, id=5, name="Сериал", name_en="Series", site="vk", save_path="/media/5",
                      url="https://vkvideo.ru/@c|q", source_type="vk_video", parser_profile_id=1)
        for unique_id, season, episode, status, filename, plan in PLAN_ROWS:
            conn.execute(
                "INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, plan_status, "
                "status, is_ignored_by_user, source_url, publication_date, slicing_status, is_available, resolution, "
                "final_filename) VALUES (5, ?, 'Серия', ?, ?, ?, ?, 0, 'https://v/1', '2026-10-20 12:00:00', 'none', 1, "
                "1080, ?)", (unique_id, season, episode, plan, status, filename))
        conn.execute("INSERT INTO video_availability (series_id, report, checked_at) VALUES (5, '{}', '2026-10-20')")
        conn.execute("INSERT INTO scan_tasks (series_id, created_at, status, task_data, results_data) "
                     "VALUES (5, '2026-01-01', 'processing', '[]', '{}')")
        conn.commit()
    return path


@pytest.fixture(scope="module")
def new_db(old_db):
    alembic(old_db, "upgrade", "0028")
    return old_db


def test_the_plan_rows_of_an_earlier_version_keep_everything_and_have_no_link_yet(new_db):
    rows = query(new_db, "SELECT * FROM media_items ORDER BY id")
    assert [(each["unique_id"], each["season"], each["episode_start"], each["plan_status"], each["status"],
             each["final_filename"]) for each in rows] == [
        (unique_id, season, episode, plan, status, filename)
        for unique_id, season, episode, status, filename, plan in PLAN_ROWS]
    assert all(each["source_id"] is None and each["row_key"] is None for each in rows)
    assert {"source_id", "row_key"} <= set(columns(new_db, "media_items"))


def test_the_report_of_players_goes_and_the_journal_of_scans_stays(new_db):
    assert "video_availability" not in names(new_db, "table")
    assert len(query(new_db, "SELECT * FROM scan_tasks")) == 1


def test_a_record_of_a_slot_is_unique_with_or_without_a_part(new_db):
    insert = ("INSERT INTO scan_references (series_id, season, episode, part, mark, source_id, row_key, unique_id) "
              "VALUES (5, 1, 1, ?, 'by_level', 1, 'k', 'u1')")
    with sqlite3.connect(new_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(insert, (None,))
        conn.execute(insert, (1,))
        conn.execute(insert, (2,))
        for part in (None, 1):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(insert, (part,))
        conn.rollback()


def test_a_mark_outside_the_five_and_a_series_that_is_not_there_are_refused(new_db):
    with sqlite3.connect(new_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO scan_references (series_id, season, episode, mark) VALUES (5, 1, 9, 'maybe')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO scan_references (series_id, season, episode, mark) VALUES (999, 1, 9, 'none')")
        conn.rollback()


def test_the_marks_without_a_row_of_the_plan_need_no_row(new_db):
    with sqlite3.connect(new_db) as conn:
        for mark in ("none", "unfit"):
            conn.execute("INSERT INTO scan_references (series_id, season, episode, mark, reason) "
                         "VALUES (5, 3, ?, ?, '{\"code\":\"reference_none\"}')", (1 if mark == "none" else 2, mark))
        conn.rollback()


def test_going_back_restores_the_report_table_and_keeps_every_row_of_the_plan(old_db):
    alembic(old_db, "upgrade", "0028")
    alembic(old_db, "downgrade", "0027")
    assert "scan_references" not in names(old_db, "table") and "media_items_row" not in names(old_db, "index")
    assert "video_availability" in names(old_db, "table")
    assert query(old_db, "SELECT * FROM video_availability") == []
    assert {"source_id", "row_key"}.isdisjoint(columns(old_db, "media_items"))
    assert [each["unique_id"] for each in query(old_db, "SELECT unique_id FROM media_items ORDER BY id")] == [
        unique_id for unique_id, *_ in PLAN_ROWS]
    alembic(old_db, "upgrade", "0028")
    assert "scan_references" in names(old_db, "table")
