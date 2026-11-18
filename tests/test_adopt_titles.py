"""The one-time transfer of the sources of protocol 1 (a card with many lines) to the sources of protocol 2 (a line of a title) by
tools/adopt_titles.py, proved on a database of the current schema filled with what a tracker keeps of such sources. Deleted in the
commit of the deployment, with the tool."""
from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from provider_mock import MockProvider, serving
from schema_db import ROOT, migrated

sys.path.insert(0, str(ROOT / "tools"))
import adopt_titles as adopt  # noqa: E402

TITLE = "title://mock/abc123"
X, Y = f"{TITLE}/X", f"{TITLE}/Y"
LINES = ("X", "Y")


def token(line: str, n: int) -> str:
    return f"{line}-tok-{n}"


def make_db(path: str) -> sqlite3.Connection:
    """Two series. The first holds two old sources: a card of the door alpha (the lines X, Y and Z) and one of the door beta (the line X
    only); the second holds the card of alpha. The source 10 has its own settings, which the sources made of it must keep."""
    conn = sqlite3.connect(migrated(path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    for sid in (1, 2):
        conn.execute("INSERT INTO series (id, name, name_en, site, save_path, auto_scan_enabled) VALUES (?, ?, ?, 'x', '/lib', 0)", (sid, f"S{sid}", f"S{sid}"))
    sources = [(10, 1, 0, "app://alpha/101"), (11, 1, 1, "app://beta/201"), (20, 2, 0, "app://alpha/101")]
    for source_id, series_id, position, url in sources:
        conn.execute("INSERT INTO series_sources (id, series_id, position, url, source_type, color, rows_per_line, players_off) "
                     "VALUES (?, ?, ?, ?, 'web_video', ?, '-10', '[\"line:Z\"]')", (source_id, series_id, position, url, source_id))
    for url, door in (("app://alpha/101", "alpha"), ("app://beta/201", "beta")):
        conn.execute("INSERT INTO release_tables (url, site, names, state, complete, title) VALUES (?, ?, '[\"t\"]', 'ready', 1, ?)", (url, door, door))
        seq = 0
        for line in LINES:
            if door == "beta" and line == "Y":
                continue  # the second door does not carry the line Y
            for n in range(1, 4):
                seq += 1
                conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (url, seq, f"{door}:{line}:{n}", f"line:{line}", str(n), "playable",
                              f"app://{door}/1#line={line}&item={token(line, n)}", token(line, n)))
    # two old rows the provider has no video for: the line Z it does not play at all, a row of Y its table does not have
    for seq, key, player, video in ((98, "alpha:Y:9", "line:Y", "gone"), (99, "alpha:Z:1", "line:Z", "z1")):
        conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     ("app://alpha/101", seq, key, player, "1", "playable", f"app://alpha/101#item={video}", video))
    for source_id, series_id, _p, url in sources:
        conn.execute("INSERT INTO composition_sources (source_id, series_id, position, table_url, summary) VALUES (?, ?, 0, ?, ?)",
                     (source_id, series_id, url, json.dumps({"source_id": source_id, "url": url, "canonical_url": url})))
        conn.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, player, state, link, video) VALUES (?, ?, ?, ?, 'line:X', 'playable', ?, ?)",
                     (series_id, source_id, "alpha:X:1" if "alpha" in url else "beta:X:1", source_id, f"{url}#item={token('X', 1)}", token("X", 1)))

    def asset(source_id: int, row_key: str, line: str, n: int) -> dict:
        ident = {"source_id": source_id, "row_key": row_key, "video": token(line, n)}
        return {"name": "a.mkv", "video": {"id": ident, "snapshot": {"source": "alpha", "player": f"line:{line}",
                                                                      "link": f"app://alpha/101#line={line}&item={token(line, n)}"},
                                           "timeline": dict(ident)},
                "tracks": [{"aligned_to": dict(ident)}]}

    for name, entry in (("a.mkv", asset(10, "alpha:X:1", "X", 1)), ("b.mkv", asset(11, "beta:X:2", "X", 2)),  # on the source that is merged
                        ("c.mkv", asset(10, "alpha:Y:2", "Y", 2))):  # on the line that becomes a source of its own
        conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, ?, ?)", (name, json.dumps(entry)))
    conn.execute("INSERT INTO plan_failures (series_id, key) VALUES (1, ?)",
                 (json.dumps(["align", [11, "beta:X:3", token("X", 3)], [10, "alpha:X:3", token("X", 3)]], separators=(",", ":")),))
    return conn


def targets() -> adopt.Targets:
    return {"app://alpha/101": [("X", X), ("Y", Y)], "app://beta/201": [("X", X)]}


def tables() -> dict[str, dict]:
    return {f"{TITLE}/{line}": {"title": f"名 · {line}", "names": ["名"], "rows": [
        {"key": f"{line}:{n}", "player": f"line:{line}", "video": token(line, n), "link": f"line://{line}/{token(line, n)}"} for n in range(1, 4)]}
        for line in LINES}


def plan_of(conn: sqlite3.Connection) -> adopt.Plan:
    return adopt.build_plan(adopt.old_rows(conn), targets(), tables())


def leftovers(conn: sqlite3.Connection) -> dict[str, int]:
    found = {}
    for table, column in (("series_sources", "url"), ("release_tables", "url"), ("release_rows", "url"), ("release_rows", "link"),
                          ("composition_sources", "table_url"), ("composition_rows", "link"), ("plan_assets", "data"), ("plan_failures", "key")):
        found[f"{table}.{column}"] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE "{column}" LIKE \'%app://%\'').fetchone()[0]
    for table, column in (("release_rows", "key"), ("composition_rows", "row_key")):
        found[f"{table}.{column}"] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE "{column}" LIKE \'alpha:%\' OR "{column}" LIKE \'beta:%\'').fetchone()[0]
    return {key: n for key, n in found.items() if n}


def source_of(conn: sqlite3.Connection, series_id: int, url: str) -> int:
    return conn.execute("SELECT id FROM series_sources WHERE series_id = ? AND url = ?", (series_id, url)).fetchone()[0]


def test_the_old_rows_of_each_line_are_matched_to_the_rows_of_its_line_source_by_player_and_video(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    plan = plan_of(conn)
    alpha_x = next(each for each in plan.moves if each.old_url == "app://alpha/101" and each.line == "X")
    assert alpha_x.new_url == X and alpha_x.keys["alpha:X:2"] == ("X:2", f"line://X/{token('X', 2)}") and alpha_x.lost == []
    alpha_y = next(each for each in plan.moves if each.old_url == "app://alpha/101" and each.line == "Y")
    assert sorted(alpha_y.keys) == [f"alpha:Y:{n}" for n in (1, 2, 3)] and alpha_y.lost == ["alpha:Y:9"]  # a row its line has no video for
    assert plan.dropped == {"app://alpha/101": ["alpha:Z:1"], "app://beta/201": []}  # a line the provider has no target for
    beta = next(each for each in plan.moves if each.old_url == "app://beta/201")
    assert sorted(beta.keys) == [f"beta:X:{n}" for n in (1, 2, 3)] and beta.lost == []


def test_a_card_becomes_a_source_for_each_of_its_lines_keeping_the_settings_and_the_number_of_the_main_line(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    report = adopt.apply(conn, targets(), plan_of(conn), tables(), allow_orphans=True)  # nothing stands on the lost rows

    assert leftovers(conn) == {}  # nothing of protocol 1 is left in a place that holds it
    sources = {(series_id, url): sid for sid, series_id, url in conn.execute("SELECT id, series_id, url FROM series_sources")}
    assert sorted(sources) == [(1, X), (1, Y), (2, X), (2, Y)]
    assert (sources[(1, X)], sources[(2, X)]) == (10, 20)  # the old numbers stay with the line with most rows (ties by code)
    assert sources[(1, Y)] not in (10, 11, 20) and sources[(2, Y)] not in (10, 11, 20)
    assert (report.sources_moved, report.sources_created, report.sources_merged) == (2, 2, 1)  # the source 11 is the line X of the series 1 again
    assert {tuple(each) for each in conn.execute("SELECT series_id, rows_per_line, players_off FROM series_sources")} == {(1, "-10", '["line:Z"]'), (2, "-10", '["line:Z"]')}
    made = conn.execute("SELECT color, position FROM series_sources WHERE id = ?", (sources[(1, Y)],)).fetchone()
    assert made[0] == 10 and made[1] == 2  # the settings of the source it was made of, after the sources the series had

    assert {each[0] for each in conn.execute("SELECT url FROM release_tables")} == {X, Y}
    assert conn.execute("SELECT site, state, title FROM release_tables WHERE url = ?", (Y,)).fetchone() == ("chines", "ready", "名 · Y")
    for url, line in ((X, "X"), (Y, "Y")):
        keys = [each[0] for each in conn.execute('SELECT "key" FROM release_rows WHERE url = ? ORDER BY seq', (url,))]
        assert keys == [f"{line}:{n}" for n in (1, 2, 3)]  # the rows of both old tables are one table of the line; the lost rows are not there
        seqs = [each[0] for each in conn.execute("SELECT seq FROM release_rows WHERE url = ?", (url,))]
        assert len(seqs) == len(set(seqs))
    assert conn.execute("SELECT COUNT(*) FROM release_rows WHERE link LIKE 'line://%'").fetchone()[0] == 6
    assert (report.tables, report.rows_moved, report.rows_lost) == (2, 6, 2)
    assert conn.execute("SELECT COUNT(*) FROM composition_rows").fetchone()[0] == 0  # derived: the service counts them again at its start
    assert conn.execute("SELECT COUNT(*) FROM composition_sources").fetchone()[0] == 0


def test_the_files_of_the_plan_follow_their_rows_to_the_source_of_their_line(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    adopt.apply(conn, targets(), plan_of(conn), tables(), allow_orphans=True)
    y_source = source_of(conn, 1, Y)
    entries = {name: json.loads(data) for name, data in conn.execute("SELECT name, data FROM plan_assets")}

    for name, source_id, row_key, line, n in (("a.mkv", 10, "X:1", "X", 1), ("b.mkv", 10, "X:2", "X", 2),  # the file of the merged source
                                              ("c.mkv", y_source, "Y:2", "Y", 2)):                          # the file of the line that split off
        entry = entries[name]["video"]
        assert entry["id"] == {"source_id": source_id, "row_key": row_key, "video": token(line, n)}
        assert entry["timeline"]["row_key"] == row_key and entry["timeline"]["source_id"] == source_id
        assert entries[name]["tracks"][0]["aligned_to"]["source_id"] == source_id
        assert entry["snapshot"]["link"] == f"line://{line}/{token(line, n)}"
    (failure,) = conn.execute("SELECT key FROM plan_failures").fetchall()
    assert json.loads(failure[0]) == ["align", [10, "X:3", token("X", 3)], [10, "X:3", token("X", 3)]]


def test_the_transfer_run_again_changes_nothing(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    adopt.apply(conn, targets(), plan_of(conn), tables(), allow_orphans=True)
    before = [tuple(each) for each in conn.execute("SELECT id, series_id, url FROM series_sources ORDER BY id")]
    again = adopt.apply(conn, targets(), plan_of(conn), tables())
    assert (again.sources_moved, again.sources_created, again.rows_moved, again.assets_rewritten, again.failures_rewritten) == (0, 0, 0, 0, 0)
    assert [tuple(each) for each in conn.execute("SELECT id, series_id, url FROM series_sources ORDER BY id")] == before


def test_a_line_the_series_already_has_is_not_made_a_second_source(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    conn.execute("INSERT INTO series_sources (id, series_id, position, url, source_type) VALUES (30, 2, 5, ?, 'web_video')", (Y,))  # added by hand
    report = adopt.apply(conn, targets(), plan_of(conn), tables(), allow_orphans=True)
    assert source_of(conn, 2, Y) == 30 and report.sources_created == 1  # only the series 1 got a new source for the line Y


@pytest.mark.parametrize("row_key", ["alpha:Z:1", "alpha:Y:9"])
def test_a_file_that_stands_on_a_row_the_provider_has_no_video_for_is_refused_and_nothing_is_written(tmp_path, row_key):
    conn = make_db(str(tmp_path / "a.db"))
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'z.mkv', ?)", (json.dumps(
        {"video": {"id": {"source_id": 10, "row_key": row_key, "video": "z1"}}}),))
    before = [tuple(each) for each in conn.execute("SELECT id, url FROM series_sources ORDER BY id")]
    with pytest.raises(adopt.Refused, match="1 files of the plan"):
        adopt.apply(conn, targets(), plan_of(conn), tables())
    assert [tuple(each) for each in conn.execute("SELECT id, url FROM series_sources ORDER BY id")] == before
    assert conn.execute("SELECT COUNT(*) FROM composition_rows").fetchone()[0] == 3 and leftovers(conn) != {}
    report = adopt.apply(conn, targets(), plan_of(conn), tables(), allow_orphans=True)  # the owner took it
    assert report.orphaned and leftovers(conn).get("series_sources.url") is None


async def test_the_provider_is_asked_for_the_line_sources_and_their_rows_and_what_it_cannot_adopt_stays_as_it_was(tmp_path):
    mock = MockProvider()
    mock.add_site("mock", "Mock")
    mock.title("mock", "abc123", ["名"], [("1", "X", token("X", 1), [1080]), ("2", "X", token("X", 2), [720]), ("1", "Y", token("Y", 1), [1080])])
    mock.adoptions["app://alpha/101"] = {"url": "app://alpha/101", "targets": [{"line": "X", "to": X}, {"line": "Y", "to": Y}], "skipped": ["Z"]}
    async with serving(mock) as server:
        found, rows = await adopt.ask_provider(server.url, "", ["app://alpha/101", "app://gone/1"])
    assert found == {"app://alpha/101": [("X", X), ("Y", Y)]}
    # each line is read alone: the rows of the other are not in its table (the key is the place of the row in the title of the mock)
    assert [each["key"] for each in rows[X]["rows"]] == ["X:1", "X:2"] and [each["key"] for each in rows[Y]["rows"]] == ["Y:3"]
    assert rows[X]["rows"][0]["link"] == f"line://X/{token('X', 1)}"

    conn = make_db(str(tmp_path / "a.db"))
    plan = adopt.build_plan(adopt.old_rows(conn), found, rows)
    report = adopt.apply(conn, found, plan, rows, allow_orphans=True)
    assert report.unadopted == ["app://beta/201"] and conn.execute("SELECT url FROM series_sources WHERE id = 11").fetchone() == ("app://beta/201",)
