"""Migration 0033: a source gets the name of its voice-over, empty for every source that exists, and loses it on the way back."""
import sqlite3
import subprocess
import sys

from series_factory import insert_series


def alembic(path, *args):
    subprocess.run([sys.executable, "-m", "alembic", *args],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=".", check=True,
                   capture_output=True)


def columns(path):
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute("PRAGMA table_info(series_sources)")]


def test_the_name_is_empty_for_every_source_that_exists_and_the_other_columns_stay(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0032")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", url="https://site/title.html",
                      source_type="web_video")
        before = conn.execute("SELECT * FROM series_sources").fetchall()
    assert before and "voice_name" not in columns(path)
    alembic(path, "upgrade", "0033")
    assert columns(path)[-1] == "voice_name"
    with sqlite3.connect(path) as conn:
        after = conn.execute("SELECT * FROM series_sources").fetchall()
    assert [row[:-1] for row in after] == before and {row[-1] for row in after} == {""}
    alembic(path, "downgrade", "0032")
    with sqlite3.connect(path) as conn:
        assert "voice_name" not in columns(path)
        assert conn.execute("SELECT * FROM series_sources").fetchall() == before
