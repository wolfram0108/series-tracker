"""Migration 0049 of voices (one-off: removed in the commit that deploys it): a voice-over recorded as failed for want of any
source becomes absent, without an error; a voice-over that failed for another reason stays failed."""
import sqlite3
import subprocess
import sys

from series_factory import insert_series


def alembic(path, *args):
    return subprocess.run([sys.executable, "-m", "alembic", *args], env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                          cwd=".", check=True, capture_output=True, text=True)


def test_a_voice_over_failed_for_want_of_any_source_becomes_absent_and_another_failure_stays(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0048")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="a", name_en="a", site="dunhuatv", url="https://a.test/1", source_type="web_video")
        for episode, error in ((1, '{"code":"not_in_any_source"}'), (2, '{"code":"download_failed"}')):
            conn.execute("INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, status, error, "
                         "updated_at, admitted) VALUES (7, 1, ?, 0, 'A', 'A', '[]', 'failed', ?, '2026-11-06', 1)", (episode, error))
        conn.commit()
    alembic(path, "upgrade", "0049")
    with sqlite3.connect(path) as conn:
        got = conn.execute("SELECT episode, status, error FROM voice_dubs ORDER BY episode").fetchall()
    assert got == [(1, "absent", None), (2, "failed", '{"code":"download_failed"}')]
