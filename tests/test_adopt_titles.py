"""The one-time transfer of the sources of protocol 1 to the titles of protocol 2 (tools/adopt_titles.py), proved on a database of the
current schema filled with what a tracker keeps of such sources. Deleted in the commit of the deployment, with the tool."""
from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from provider_mock import MockProvider, serving
from schema_db import ROOT, migrated

sys.path.insert(0, str(ROOT / "tools"))
import adopt_titles as adopt  # noqa: E402

NEW = "title://mock/abc123"
LINES = ("X", "Y")


def token(line: str, n: int) -> str:
    return f"{line}-tok-{n}"


def make_db(path: str) -> sqlite3.Connection:
    """Two series: the first holds two old sources that come to one title (a second door's card), the second one of them."""
    conn = sqlite3.connect(migrated(path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    for sid in (1, 2):
        conn.execute("INSERT INTO series (id, name, name_en, site, save_path, auto_scan_enabled) VALUES (?, ?, ?, 'x', '/lib', 0)", (sid, f"S{sid}", f"S{sid}"))
    sources = [(10, 1, 0, "app://alpha/101"), (11, 1, 1, "app://beta/201"), (20, 2, 0, "app://alpha/101")]
    for source_id, series_id, position, url in sources:
        conn.execute("INSERT INTO series_sources (id, series_id, position, url, source_type) VALUES (?, ?, ?, ?, 'web_video')", (source_id, series_id, position, url))
    for url, door in (("app://alpha/101", "alpha"), ("app://beta/201", "beta")):
        conn.execute("INSERT INTO release_tables (url, site, names, state, complete, title) VALUES (?, ?, '[\"t\"]', 'ready', 1, ?)", (url, door, door))
        for line in LINES:
            for n in range(1, 4):
                if door == "beta" and line == "Y":
                    continue  # the second door does not carry the line Y
                key = f"{door}:{line}:{n}"
                conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (url, len(conn.execute("SELECT 1 FROM release_rows WHERE url = ?", (url,)).fetchall()) + 1, key, f"line:{line}", str(n),
                              "playable", f"app://{door}/1#line={line}&item={token(line, n)}", token(line, n)))
    # an extra old row the title has no video for (the line Z is not served any more)
    conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, 99, ?, ?, ?, ?, ?, ?)',
                 ("app://alpha/101", "alpha:Z:1", "line:Z", "1", "playable", "app://alpha/101#line=Z&item=z1", "z1"))
    for source_id, series_id, _p, url in sources:
        conn.execute("INSERT INTO composition_sources (source_id, series_id, position, table_url, summary) VALUES (?, ?, 0, ?, ?)",
                     (source_id, series_id, url, json.dumps({"source_id": source_id, "url": url, "canonical_url": url})))
        key = "alpha:X:1" if "alpha" in url else "beta:X:1"
        conn.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, player, state, link, video) VALUES (?, ?, ?, ?, 'line:X', 'playable', ?, ?)",
                     (series_id, source_id, key, source_id, f"{url}#line=X&item={token('X', 1)}", token("X", 1)))
    asset = lambda source_id, row_key: {"name": "a.mkv", "video": {"id": {"source_id": source_id, "row_key": row_key, "video": token("X", 1)},
                                         "snapshot": {"source": "alpha", "player": "line:X", "link": "app://alpha/101#line=X&item=" + token("X", 1)},
                                         "timeline": {"source_id": source_id, "row_key": row_key, "video": token("X", 1)}},
                                        "tracks": [{"aligned_to": {"source_id": source_id, "row_key": row_key, "video": token("X", 1)}}]}
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'a.mkv', ?)", (json.dumps(asset(10, "alpha:X:1")),))
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'b.mkv', ?)", (json.dumps(asset(11, "beta:X:2")),))  # on the merged source
    conn.execute("INSERT INTO plan_failures (series_id, key) VALUES (1, ?)", (json.dumps(["align", [11, "beta:X:3", token("X", 3)], [10, "alpha:X:3", token("X", 3)]], separators=(",", ":")),))
    return conn


def title_table() -> list[dict]:
    return [{"key": f"{line}:{n}", "player": f"line:{line}", "video": token(line, n), "link": f"line://{line}/{token(line, n)}"}
            for line in LINES for n in range(1, 4)]


def moves_of(conn: sqlite3.Connection) -> tuple[dict, list]:
    adopted = {"app://alpha/101": NEW, "app://beta/201": NEW}
    return adopted, adopt.build_moves(adopt.old_rows(conn), adopted, {NEW: title_table()})


def leftovers(conn: sqlite3.Connection) -> dict[str, int]:
    found = {}
    for table, column in (("series_sources", "url"), ("release_tables", "url"), ("release_rows", "url"), ("release_rows", "link"),
                          ("composition_sources", "table_url"), ("composition_rows", "link"), ("plan_assets", "data"), ("plan_failures", "key")):
        found[f"{table}.{column}"] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE "{column}" LIKE \'%app://%\'').fetchone()[0]
    for table, column in (("release_rows", "key"), ("composition_rows", "row_key")):
        found[f"{table}.{column}"] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE "{column}" LIKE \'alpha:%\' OR "{column}" LIKE \'beta:%\'').fetchone()[0]
    return {key: n for key, n in found.items() if n}


def test_the_old_rows_are_matched_to_the_rows_of_the_title_by_player_and_video_and_the_row_with_no_video_is_lost(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    _adopted, moves = moves_of(conn)
    alpha = next(each for each in moves if each.old_url == "app://alpha/101")
    assert alpha.keys["alpha:X:2"] == ("X:2", f"line://X/{token('X', 2)}") and alpha.lost == ["alpha:Z:1"]
    beta = next(each for each in moves if each.old_url == "app://beta/201")
    assert sorted(beta.keys) == [f"beta:X:{n}" for n in (1, 2, 3)] and beta.lost == []


def test_the_transfer_moves_everything_the_tracker_keeps_of_the_old_addresses_and_merges_the_sources_that_came_to_one_title(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    adopted, moves = moves_of(conn)
    report = adopt.apply(conn, adopted, moves)  # no file stands on the lost row (alpha:Z:1): the transfer is allowed

    assert leftovers(conn) == {}  # nothing of protocol 1 is left in a place that holds it
    assert [tuple(each) for each in conn.execute("SELECT id, series_id, url FROM series_sources ORDER BY id")] == [(10, 1, NEW), (20, 2, NEW)]
    assert (report.sources_moved, report.sources_merged, report.rows_lost) == (2, 1, 1)
    keys = {each[0] for each in conn.execute('SELECT "key" FROM release_rows WHERE url = ?', (NEW,))}
    assert keys == {f"{line}:{n}" for line in LINES for n in range(1, 4)}  # the rows of both old tables are one table of the title
    assert conn.execute("SELECT site, state FROM release_tables WHERE url = ?", (NEW,)).fetchone() == ("chines", "ready")
    assert conn.execute("SELECT COUNT(*) FROM release_rows WHERE url = ? AND link LIKE 'line://%'", (NEW,)).fetchone()[0] == 6
    seqs = [each[0] for each in conn.execute("SELECT seq FROM release_rows WHERE url = ? ORDER BY seq", (NEW,))]
    assert len(seqs) == len(set(seqs))  # no row of the merged table lost its place to the sequence of another
    assert {each[0] for each in conn.execute("SELECT source_id FROM composition_rows")} == {10, 20}

    first, second = (json.loads(each[0]) for each in conn.execute("SELECT data FROM plan_assets ORDER BY name"))
    for entry, source_id, row_key in ((first, 10, "X:1"), (second, 10, "X:2")):  # the file of the merged source stands on the one that stayed
        assert entry["video"]["id"] == {"source_id": source_id, "row_key": row_key, "video": token("X", 1)}
        assert entry["video"]["timeline"]["row_key"] == row_key and entry["tracks"][0]["aligned_to"]["source_id"] == source_id
    assert first["video"]["snapshot"]["link"] == f"line://X/{token('X', 1)}"
    (failure,) = conn.execute("SELECT key FROM plan_failures").fetchall()
    assert json.loads(failure[0]) == ["align", [10, "X:3", token("X", 3)], [10, "X:3", token("X", 3)]]

    again = adopt.apply(conn, adopted, adopt.build_moves(adopt.old_rows(conn), adopted, {NEW: title_table()}))
    assert (again.sources_moved, again.rows_moved, again.assets_rewritten) == (0, 0, 0)  # run twice, nothing happens twice


def test_a_file_that_stands_on_a_row_the_title_has_no_video_for_is_refused_and_nothing_is_written(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'z.mkv', ?)", (json.dumps(
        {"video": {"id": {"source_id": 10, "row_key": "alpha:Z:1", "video": "z1"}}}),))
    adopted, moves = moves_of(conn)
    before = [tuple(each) for each in conn.execute("SELECT id, url FROM series_sources ORDER BY id")]
    with pytest.raises(adopt.Refused, match="1 files of the plan"):
        adopt.apply(conn, adopted, moves)
    assert [tuple(each) for each in conn.execute("SELECT id, url FROM series_sources ORDER BY id")] == before
    assert leftovers(conn) != {}
    report = adopt.apply(conn, adopted, moves, allow_orphans=True)  # the owner took it
    assert report.orphaned and leftovers(conn).get("series_sources.url") is None


async def test_the_provider_is_asked_for_the_titles_and_their_rows_and_what_it_cannot_adopt_stays_as_it_was(tmp_path):
    mock = MockProvider()
    mock.add_site("mock", "Mock")
    mock.title("mock", "abc123", ["名"], [("1", "X", token("X", 1), [1080]), ("2", "X", token("X", 2), [720])])
    mock.adoptions["app://alpha/101"] = {"url": "app://alpha/101", "to": NEW, "lines": ["X"]}
    async with serving(mock) as server:
        adopted, tables = await adopt.ask_provider(server.url, "", ["app://alpha/101", "app://gone/1"])
    assert adopted == {"app://alpha/101": NEW} and [each["key"] for each in tables[NEW]] == ["X:1", "X:2"]
    assert [each["link"] for each in tables[NEW]][0] == f"line://X/{token('X', 1)}"

    conn = make_db(str(tmp_path / "a.db"))
    report = adopt.apply(conn, adopted, adopt.build_moves(adopt.old_rows(conn), adopted, tables))
    assert report.unadopted == ["app://beta/201"] and conn.execute("SELECT url FROM series_sources WHERE id = 11").fetchone() == ("app://beta/201",)
