"""Migration 0048 of catalog (one-off: removed in the commit that deploys it): the sources there are get their colours in
the order of their series, the first 0, the next 1, and the colours of one series do not depend on another."""
import sqlite3
import subprocess
import sys

from series_factory import insert_series


def alembic(path, *args):
    return subprocess.run([sys.executable, "-m", "alembic", *args], env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                          cwd=".", check=True, capture_output=True, text=True)


def test_the_sources_there_are_get_their_colours_by_their_place_in_their_series(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0047")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="a", name_en="a", site="dunhuatv", url="https://a.test/1", source_type="web_video")
        insert_series(conn, id=8, name="b", name_en="b", site="dunhuatv", url="https://b.test/1", source_type="web_video")
        for series_id, position, url in ((7, 5, "https://a.test/3"), (7, 2, "https://a.test/2"), (8, 0, "https://b.test/2")):
            conn.execute("INSERT INTO series_sources (series_id, position, url, source_type, players_off) "
                         "VALUES (?, ?, ?, 'web_video', '[]')", (series_id, position, url))
        conn.commit()
    alembic(path, "upgrade", "0048")
    with sqlite3.connect(path) as conn:
        got = conn.execute("SELECT series_id, url, color FROM series_sources ORDER BY series_id, position, id").fetchall()
    assert got == [(7, "https://a.test/1", 0), (7, "https://a.test/2", 1), (7, "https://a.test/3", 2),
                   (8, "https://b.test/1", 0), (8, "https://b.test/2", 1)]
