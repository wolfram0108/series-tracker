"""Migration 0034 of downloads: the work path of a task and the identity of its work file, the name a ready file is on its way to.
Up, the path an earlier task wrote is written down (not guessed at run time); down, nothing that has no place in the earlier
schema is lost: whole files that wait for their names in work files it cannot name, and moves that are not finished, stop it."""
import sqlite3
import subprocess
import sys

import pytest

from series_factory import insert_series

LEGACY = "/lib/Show/.u1.Show s01e01.mp4"


def alembic(path, *args, check=True):
    return subprocess.run([sys.executable, "-m", "alembic", *args], env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                          cwd=".", check=check, capture_output=True, text=True)


def columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def revision(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def task(path, key, status, save_path="/lib/Show/Show s01e01.mp4", **fields):
    with sqlite3.connect(path) as conn:
        names = ["task_key", "series_id", "video_url", "save_path", "status", "task_type", *fields]
        conn.execute(f"INSERT INTO download_tasks ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                     (key, 7, "u", save_path, status, "vk_video", *fields.values()))
        conn.commit()


def work_paths(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT task_key, work_path FROM download_tasks"))


@pytest.fixture
def at_0033(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0033")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", url="https://site/title.html", source_type="web_video")
    return path


def test_the_upgrade_writes_the_path_an_earlier_task_wrote_and_adds_the_columns(at_0033):
    task(at_0033, "u1", "publishing")
    task(at_0033, "u2", "pending", save_path="/lib/Show/Show s01e02.mp4")

    alembic(at_0033, "upgrade", "0034")

    assert work_paths(at_0033) == {"u1": LEGACY, "u2": "/lib/Show/.u2.Show s01e02.mp4"}
    assert {"work_path", "work_size", "work_ino"} <= columns(at_0033, "download_tasks") and "rename_to" in columns(at_0033, "media_items")


def test_the_downgrade_refuses_while_a_whole_file_waits_in_a_work_file_the_earlier_schema_cannot_name(at_0033):
    alembic(at_0033, "upgrade", "0034")
    task(at_0033, "u1", "publishing", work_path="/lib/Show/.u1.mp4", work_size=5, work_ino=9)  # a work file of the new kind

    refused = alembic(at_0033, "downgrade", "0033", check=False)

    assert refused.returncode != 0 and "downgrade refused" in refused.stderr
    assert revision(at_0033) == "0034" and work_paths(at_0033) == {"u1": "/lib/Show/.u1.mp4"}  # nothing was lost, nothing changed
    with sqlite3.connect(at_0033) as conn:
        conn.execute("DELETE FROM download_tasks")  # the publication was finished
        conn.commit()
    alembic(at_0033, "downgrade", "0033")
    assert "work_path" not in columns(at_0033, "download_tasks")


def test_the_downgrade_goes_through_for_tasks_that_wrote_where_the_earlier_schema_says_and_for_unfinished_downloads(at_0033):
    alembic(at_0033, "upgrade", "0034")
    task(at_0033, "u1", "publishing", work_path=LEGACY)
    task(at_0033, "u2", "downloading", save_path="/lib/Show/Show s01e02.mp4", work_path="/lib/Show/.u2.mp4")  # a piece, not a whole file

    alembic(at_0033, "downgrade", "0033")
    alembic(at_0033, "upgrade", "0034")

    assert work_paths(at_0033) == {"u1": LEGACY, "u2": "/lib/Show/.u2.Show s01e02.mp4"}  # the path of the earlier schema, written down


def test_the_downgrade_refuses_while_a_rename_is_not_finished(at_0033):
    alembic(at_0033, "upgrade", "0034")
    with sqlite3.connect(at_0033) as conn:
        conn.execute("INSERT INTO media_items (series_id, unique_id, source_title, episode_start, plan_status, status, final_filename, "
                     "rename_to, slicing_status, is_available, is_ignored_by_user, publication_date, source_url) VALUES (7, 'u1', 't', 1, "
                     "'in_plan_single', 'completed', 'a.mp4', 'b.mp4', 'none', 1, 0, '2026-01-01 00:00:00', 'https://v')")
        conn.commit()

    refused = alembic(at_0033, "downgrade", "0033", check=False)

    assert refused.returncode != 0 and "renames are not finished" in refused.stderr and revision(at_0033) == "0034"
