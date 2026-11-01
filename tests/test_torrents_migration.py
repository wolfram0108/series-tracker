"""Migration 0031: the progress of torrents moves from download_tasks into a table of torrents, proved on data.

The database is brought to revision 0030 and filled with a series, the video tasks of downloads and the rows of
torrents in download_tasks (one hash twice, as an old double write could leave it), then migrated up, down and up by
the real alembic. What is checked is what the owners would lose: every torrent keeps its state, the video tasks are
not touched, and downgrade gives the old code the rows it wrote.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from series_factory import insert_series

ROOT = Path(__file__).resolve().parent.parent


def alembic(db_path, *args):
    done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, capture_output=True, text=True,
                          env={"ST_DB_URL": f"sqlite:///{db_path}", "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr


def query(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]


def tables(db_path):
    return {row["name"] for row in query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "old.db"
    alembic(path, "upgrade", "0030")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=5, name="Сериал", name_en="Series", site="kinozal", save_path="/media/5",
                      url="https://kinozal.tv/details.php?id=5", source_type="torrent")
        insert_series(conn, id=6, name="Видео", name_en="Video", site="vk", save_path="/media/6",
                      url="https://vkvideo.ru/@c|q", source_type="vk_video")
        conn.executemany(
            "INSERT INTO download_tasks (task_key, series_id, video_url, save_path, status, error_message, attempts, "
            "created_at, updated_at, task_type, progress, dlspeed, eta, total_size_mb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("v1", 6, "https://vk.com/video-1_1", "/media/6/e01.mp4", "downloading", None, 1, "2026-10-01 10:00:00",
              "2026-10-01 10:05:00", "vk_video", 50, 2048, 30, 120.5),
             ("hash-a", 5, None, None, "downloading", None, 0, "2026-10-02 10:00:00", "2026-10-02 10:01:00",
              "torrent", 30, 1000, 60, None),
             ("hash-a", 5, None, None, "uploading", None, 0, "2026-10-02 10:00:00", "2026-10-02 10:09:00",
              "torrent", 100, 0, 0, None),
             ("v2", 6, "https://vk.com/video-1_2", "/media/6/e02.mp4", "error", "обрыв", 3, "2026-10-01 11:00:00",
              "2026-10-01 11:05:00", "vk_video", 0, 0, 0, None),
             ("hash-b", 5, None, None, "missingFiles", None, 0, "2026-10-03 10:00:00", "2026-10-03 10:01:00",
              "torrent", 12, 0, 0, None)])
        conn.commit()
    return path


def video_tasks(path):
    return [(r["task_key"], r["status"], r["progress"], r["attempts"], r["save_path"]) for r in query(
        path, "SELECT * FROM download_tasks WHERE task_type = 'vk_video' ORDER BY id")]


def test_up_moves_the_torrents_and_leaves_the_video_tasks_as_they_were(old_db):
    alembic(old_db, "upgrade", "0031")

    assert "torrent_progress" in tables(old_db)
    moved = query(old_db, "SELECT * FROM torrent_progress ORDER BY id")
    assert [(r["qb_hash"], r["series_id"], r["status"], r["progress"], r["dlspeed"], r["eta"]) for r in moved] == [
        ("hash-a", 5, "uploading", 100, 0, 0), ("hash-b", 5, "missingFiles", 12, 0, 0)]  # the latest row of a hash
    assert moved[0]["created_at"] == "2026-10-02 10:00:00" and moved[0]["updated_at"] == "2026-10-02 10:09:00"
    assert query(old_db, "SELECT COUNT(*) AS n FROM download_tasks WHERE task_type = 'torrent'")[0]["n"] == 0
    assert [r["task_key"] for r in query(old_db, "SELECT task_key FROM download_tasks ORDER BY id")] == ["v1", "v2"]
    assert [(r["task_key"], r["status"], r["progress"], r["attempts"], r["save_path"]) for r in query(
        old_db, "SELECT * FROM download_tasks ORDER BY id")] == [
        ("v1", "downloading", 50, 1, "/media/6/e01.mp4"), ("v2", "error", 0, 3, "/media/6/e02.mp4")]


def test_the_table_keeps_one_row_per_hash_and_a_series_for_each_row(old_db):
    alembic(old_db, "upgrade", "0031")

    with sqlite3.connect(old_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-a', 5, 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-c', 99, 'x')")
    assert {row["name"] for row in query(old_db, "SELECT name FROM sqlite_master WHERE type='index'")} >= {
        "ix_torrent_progress_series"}


def test_down_gives_the_old_code_the_rows_it_wrote_and_up_again_loses_nothing(old_db):
    alembic(old_db, "upgrade", "0031")
    after_up = query(old_db, "SELECT qb_hash, series_id, status, progress, dlspeed, eta FROM torrent_progress")

    alembic(old_db, "downgrade", "0030")

    assert "torrent_progress" not in tables(old_db)
    back = query(old_db, "SELECT * FROM download_tasks WHERE task_type = 'torrent' ORDER BY id")
    assert [(r["task_key"], r["series_id"], r["status"], r["progress"], r["dlspeed"], r["eta"]) for r in back] == [
        (r["qb_hash"], r["series_id"], r["status"], r["progress"], r["dlspeed"], r["eta"]) for r in after_up]
    assert all(r["attempts"] == 0 and r["video_url"] is None and r["save_path"] is None for r in back)
    assert [r[0] for r in video_tasks(old_db)] == ["v1", "v2"]

    alembic(old_db, "upgrade", "0031")

    assert query(old_db, "SELECT qb_hash, series_id, status, progress, dlspeed, eta FROM torrent_progress") == after_up
    assert query(old_db, "SELECT COUNT(*) AS n FROM download_tasks WHERE task_type = 'torrent'")[0]["n"] == 0
    assert [r[0] for r in video_tasks(old_db)] == ["v1", "v2"]


def test_a_database_without_torrent_rows_migrates_both_ways(tmp_path):
    path = tmp_path / "empty.db"
    alembic(path, "upgrade", "0031")
    assert query(path, "SELECT COUNT(*) AS n FROM torrent_progress")[0]["n"] == 0
    alembic(path, "downgrade", "0030")
    assert "torrent_progress" not in tables(path)
