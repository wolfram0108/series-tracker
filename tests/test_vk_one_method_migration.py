"""Migration 0041: a VK channel is read one way. The search mode leaves the sources, and the keys of the tables of a channel and
the addresses the composition keeps lose it alike; the tables keep their rows."""
import sqlite3
import subprocess
import sys

from series_factory import insert_series

CHANNEL = "https://vkvideo.ru/@pvashow"


def alembic(path, *args):
    subprocess.run([sys.executable, "-m", "alembic", *args],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=".", check=True,
                   capture_output=True)


def table(conn, url: str, rows: int, site: str = "vk_channel") -> None:
    conn.execute("INSERT INTO release_tables (url, site, read_at, complete, state) VALUES (?, ?, '2026-11-05', 1, 'ready')",
                 (url, site))
    for seq in range(rows):
        conn.execute("INSERT INTO release_rows (url, seq, key, player, state) VALUES (?, ?, ?, 'vk', 'playable')",
                     (url, seq, f"k{seq}"))


def tables(conn) -> dict[str, int]:
    return dict(conn.execute("SELECT t.url, (SELECT COUNT(*) FROM release_rows r WHERE r.url = t.url) FROM release_tables t"))


def test_the_mode_leaves_the_sources_the_tables_and_the_composition_and_comes_back_on_the_way_down(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0040")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=46, name="s", name_en="s", site="vk", url=f"{CHANNEL}|Боевой мастер", source_type="vk_video")
        source_id = conn.execute("SELECT id FROM series_sources").fetchone()[0]
        table(conn, f"{CHANNEL}|Боевой мастер|search", 3)          # the table the composition points to
        table(conn, f"{CHANNEL}|Боевой мастер|get_all", 5)         # its twin of the other mode
        table(conn, f"{CHANNEL}|Царь небес|get_all", 2)            # a channel read in one mode only
        table(conn, f"{CHANNEL}||search", 9)                       # a channel with no query: no page any more
        table(conn, "https://1.ruchime.org/release/x", 4, site="ruchime")
        conn.execute("INSERT INTO composition_sources (source_id, series_id, position, table_url, summary) "
                     "VALUES (?, 46, 0, ?, '{}')", (source_id, f"{CHANNEL}|Боевой мастер|search"))

    alembic(path, "upgrade", "0041")

    with sqlite3.connect(path) as conn:
        assert "vk_search_mode" not in [row[1] for row in conn.execute("PRAGMA table_info(series_sources)")]
        assert conn.execute("SELECT url FROM series_sources").fetchall() == [(f"{CHANNEL}|Боевой мастер",)]
        assert tables(conn) == {f"{CHANNEL}|Боевой мастер": 3, f"{CHANNEL}|Царь небес": 2,
                                "https://1.ruchime.org/release/x": 4}
        assert conn.execute("SELECT COUNT(*) FROM release_rows").fetchone()[0] == 9
        assert conn.execute("SELECT table_url FROM composition_sources").fetchall() == [(f"{CHANNEL}|Боевой мастер",)]

    alembic(path, "downgrade", "0040")

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT vk_search_mode FROM series_sources").fetchall() == [("search",)]
        assert tables(conn) == {f"{CHANNEL}|Боевой мастер|search": 3, f"{CHANNEL}|Царь небес|search": 2,
                                "https://1.ruchime.org/release/x": 4}
        assert conn.execute("SELECT table_url FROM composition_sources").fetchall() == [(f"{CHANNEL}|Боевой мастер|search",)]
