"""The one-time transfer of the files of a series to the source the user added for it (tools/adopt_to_source.py), proved on a database of
the current schema filled with what a tracker keeps of such a series. Deleted in the commit of the deployment, with the tool."""
from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from schema_db import ROOT, migrated

sys.path.insert(0, str(ROOT / "tools"))
import adopt_to_source as adopt  # noqa: E402

NEW = "title://mock/abc123/X"
TOKENS = {n: f"X_tok%2F{n}" for n in range(1, 6)}  # a token of the archive keeps its own percent codes, as the real ones do


def make_db(path: str) -> sqlite3.Connection:
    """A series of three sources of protocol 1 (two doors of one title: 1-4 and 1-3, a deleted third one: 4-5), a voice-over source of a
    site that stays (9), and the source the user added (30) whose table lists the rows 1-4 and a fifth that no old one had."""
    conn = sqlite3.connect(migrated(path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO series (id, name, name_en, site, save_path, auto_scan_enabled) VALUES (1, 'S', 'S', 'x', '/lib', 0)")
    for sid, position, url in ((10, 0, "app://alpha/101"), (11, 1, "app://beta/201"), (9, 2, "https://site.example/voices"), (30, 3, NEW)):
        conn.execute("INSERT INTO series_sources (id, series_id, position, url, source_type) VALUES (?, 1, ?, ?, 'web_video')", (sid, position, url))
    conn.execute("INSERT INTO release_tables (url, site, names, state, complete, title) VALUES (?, 'chines', '[]', 'ready', 1, 'T')", (NEW,))
    for n in range(1, 5):
        conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (NEW, n, f"X:{n}", "line:X", str(n), "playable", f"line://X/{TOKENS[n]}", TOKENS[n]))

    def ref(source_id: int, key: str, video: str) -> dict:
        return {"source_id": source_id, "row_key": key, "video": video}

    def asset(name: str, door: int, n: int, dub_source: int = 9) -> dict:
        row = ref(door, f"{'alpha' if door == 10 else 'beta'}:X:{n}", TOKENS.get(n, f"X_tok%2F{n}"))
        snap = {"source": "alpha" if door == 10 else "beta", "player": "line:X", "group": "G", "height": 2160,
                "link": f"app://alpha/101#line=X&item={TOKENS.get(n, '')}", "label": "G"}
        return {"name": name, "video": {"id": row, "snapshot": dict(snap), "episode": [1, n], "timeline": dict(row), "cut_of": None},
                "tracks": [{"dub": ref(dub_source, f"cvh:u{n}", f"cvh{n}"), "snapshot": {"source": "site.example", "player": "cvh", "group": "V",
                                                                                           "height": 1080, "link": f"https://site.example/#cvh{n}", "label": "V"},
                            "episode": [1, n], "aligned_to": dict(row)}]}

    for name, door, n in (("e1.mkv", 10, 1), ("e2.mkv", 11, 2), ("e5.mkv", 10, 5)):  # the fifth row the new source does not list
        conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, ?, ?)", (name, json.dumps(asset(name, door, n))))
    # a file whose video stood on a source that was deleted since: the reference names an id that is no source any more
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'e3.mkv', ?)", (json.dumps(asset("e3.mkv", 12, 3)),))
    conn.execute("INSERT INTO plan_failures (series_id, key) VALUES (1, ?)",
                 (json.dumps(["align", [10, "alpha:X:4", TOKENS[4]], [9, "cvh:u4", "cvh4"]], separators=(",", ":")),))
    chinese = lambda sid: {"group": "G", "source_id": sid, "player": "line:X"}
    settings = [
        ("reference.order", None, json.dumps([chinese(10), chinese(11), {"group": "V", "source_id": 9, "player": "cvh"}], ensure_ascii=False)),
        ("reference.same", None, json.dumps([[chinese(10), chinese(11)]], ensure_ascii=False)),
        ("reference.allow", 'variant:["G",10,"line:X"]', "true"),
        ("reference.allow", 'variant:["G",11,"line:X"]', "false"),  # the other door's variant of the same line: one variant after the transfer
        ("reference.allow", 'variant:["V",9,"cvh"]', "false"),
        ("reference.allow", 'variant:["G",10,"line:Z"]', "false"),  # a line no new source is given for
    ]
    for lever, target, value in settings:
        conn.execute("INSERT INTO output_settings (series_id, season, lever, target, value) VALUES (1, 1, ?, ?, ?)", (lever, target, value))
    conn.execute("INSERT INTO composition_sources (source_id, series_id, position, table_url, summary) VALUES (10, 1, 0, 'app://alpha/101', '{}')")
    conn.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, player, state, link, video) VALUES (1, 10, 'alpha:X:1', 1, 'line:X', 'playable', 'l', 'v')")
    return conn


def data_of(conn: sqlite3.Connection, name: str) -> dict:
    return json.loads(conn.execute("SELECT data FROM plan_assets WHERE series_id = 1 AND name = ?", (name,)).fetchone()[0])


def test_the_references_to_rows_of_old_or_deleted_sources_are_pointed_to_the_rows_of_the_new_source_by_line_and_token(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    report = adopt.apply(conn, 1, [30])
    first = data_of(conn, "e1.mkv")["video"]
    assert first["id"] == {"source_id": 30, "row_key": "X:1", "video": TOKENS[1]} and first["timeline"] == first["id"]
    assert first["snapshot"]["link"] == f"line://X/{TOKENS[1]}" and first["snapshot"]["source"] == "chines"  # where it came from, told as the row tells it
    assert first["snapshot"]["group"] == "G" and first["snapshot"]["height"] == 2160 and first["episode"] == [1, 1]  # the rest of what it was stays
    assert data_of(conn, "e1.mkv")["tracks"][0]["aligned_to"] == first["id"]
    assert data_of(conn, "e2.mkv")["video"]["id"]["source_id"] == 30 and data_of(conn, "e2.mkv")["video"]["id"]["row_key"] == "X:2"  # the other door's
    assert data_of(conn, "e3.mkv")["video"]["id"] == {"source_id": 30, "row_key": "X:3", "video": TOKENS[3]}  # the deleted source's file too
    assert (report.files, report.files_rewritten) == (4, 3)


def test_what_belongs_to_a_source_that_stays_and_what_the_new_source_does_not_list_is_left_as_it_was(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    before = data_of(conn, "e1.mkv")["tracks"][0]["dub"], data_of(conn, "e5.mkv")["video"]
    report = adopt.apply(conn, 1, [30])
    assert data_of(conn, "e1.mkv")["tracks"][0]["dub"] == before[0]  # the voice-over of another site is its own source's
    assert data_of(conn, "e1.mkv")["tracks"][0]["snapshot"]["link"] == "https://site.example/#cvh1"
    assert data_of(conn, "e5.mkv")["video"] == before[1]  # the fifth row is in no row of the new source: the file keeps what it knew
    assert report.unmatched == {(10, "line:X"): {"alpha:X:5"}} and report.untouched_sources == {9}


def test_a_failure_of_the_plan_names_the_new_row_and_the_derived_tables_of_the_composition_are_dropped(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    report = adopt.apply(conn, 1, [30])
    (failure,) = conn.execute("SELECT key FROM plan_failures").fetchall()
    assert json.loads(failure[0]) == ["align", [30, "X:4", TOKENS[4]], [9, "cvh:u4", "cvh4"]] and report.failures_rewritten == 1
    assert conn.execute("SELECT COUNT(*) FROM composition_rows").fetchone()[0] == 0  # the service counts them again at its start
    assert conn.execute("SELECT COUNT(*) FROM composition_sources").fetchone()[0] == 0


def test_the_transfer_run_again_changes_nothing(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    adopt.apply(conn, 1, [30])
    snapshot = [tuple(each) for each in conn.execute("SELECT name, data FROM plan_assets ORDER BY name")]
    again = adopt.apply(conn, 1, [30])
    assert (again.files_rewritten, again.refs_rewritten, again.failures_rewritten) == (0, 0, 0)
    assert [tuple(each) for each in conn.execute("SELECT name, data FROM plan_assets ORDER BY name")] == snapshot


@pytest.mark.parametrize("source, spoil, expected", [
    (99, "", "is no source of the series"),
    (30, "UPDATE release_tables SET state = 'reading'", "no table that is ready"),
    (30, "DELETE FROM release_rows", "holds no row"),
    (30, "INSERT INTO plan_runs (series_id, download) VALUES (1, 0)", "under way"),
])
def test_a_transfer_that_would_rest_on_a_wrong_ground_is_refused_and_nothing_is_written(tmp_path, source, spoil, expected):
    conn = make_db(str(tmp_path / "a.db"))
    if spoil:
        conn.execute(spoil)
    before = [tuple(each) for each in conn.execute("SELECT name, data FROM plan_assets ORDER BY name")]
    with pytest.raises(adopt.Refused, match=expected):
        adopt.apply(conn, 1, [source])
    assert [tuple(each) for each in conn.execute("SELECT name, data FROM plan_assets ORDER BY name")] == before
    assert conn.execute("SELECT COUNT(*) FROM composition_rows").fetchone()[0] == 1


def settings_of(conn: sqlite3.Connection) -> dict:
    return {(lever, target): value for lever, target, value in conn.execute("SELECT lever, target, value FROM output_settings WHERE series_id = 1")}


def test_the_variants_the_user_chose_are_pointed_to_the_new_source_of_their_line_and_two_old_variants_of_one_line_become_one(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    report = adopt.apply(conn, 1, [30])
    found = settings_of(conn)
    assert json.loads(found[("reference.order", None)]) == [{"group": "G", "source_id": 30, "player": "line:X"},
                                                              {"group": "V", "source_id": 9, "player": "cvh"}]  # the same variant once, the site's own stays
    assert json.loads(found[("reference.same", None)]) == [[{"group": "G", "source_id": 30, "player": "line:X"}]]
    assert found[("reference.allow", 'variant:["G",30,"line:X"]')] == "true"  # the first word of the user stands, the second one goes
    assert ("reference.allow", 'variant:["G",11,"line:X"]') not in found and ("reference.allow", 'variant:["G",10,"line:X"]') not in found
    assert found[("reference.allow", 'variant:["V",9,"cvh"]')] == "false"  # a source that stays
    assert found[("reference.allow", 'variant:["G",10,"line:Z"]')] == "false"  # a line with no new source: as it was
    assert (report.settings_rewritten, report.settings_merged) == (3, 1)


def test_a_file_stands_on_the_new_source_of_its_own_line_when_a_series_is_given_several(tmp_path):
    conn = make_db(str(tmp_path / "a.db"))
    other = "title://mock/abc123/Y"
    conn.execute("INSERT INTO series_sources (id, series_id, position, url, source_type) VALUES (31, 1, 4, ?, 'web_video')", (other,))
    conn.execute("INSERT INTO release_tables (url, site, names, state, complete, title) VALUES (?, 'chines', '[]', 'ready', 1, 'T')", (other,))
    conn.execute('INSERT INTO release_rows (url, seq, "key", player, text, state, link, video) VALUES (?, 1, ?, ?, ?, ?, ?, ?)',
                 (other, "Y:1", "line:Y", "1", "playable", "line://Y/y1", "y1"))
    asset = {"name": "y.mkv", "video": {"id": {"source_id": 10, "row_key": "alpha:Y:7", "video": "y1"},
                                         "snapshot": {"source": "alpha", "player": "line:Y", "group": "G", "height": 2160, "link": "", "label": "G"}}}
    conn.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (1, 'y.mkv', ?)", (json.dumps(asset),))
    adopt.apply(conn, 1, [30, 31])
    assert data_of(conn, "y.mkv")["video"]["id"] == {"source_id": 31, "row_key": "Y:1", "video": "y1"}  # a line of the other source, found by its token
    assert data_of(conn, "e1.mkv")["video"]["id"]["source_id"] == 30
