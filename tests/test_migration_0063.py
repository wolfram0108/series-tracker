"""The migration 0063 (a line of the applications is one player, `line:<code>`), proved on data of the form the
production database holds before it is run there; the test goes with the commit that runs it on production."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def migration():
    spec = importlib.util.spec_from_file_location("m0063", ROOT / "alembic" / "versions" / "0063_line_players.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_names_of_the_players_of_lines_are_renamed_wherever_they_are_kept_and_the_keys_of_rows_stay(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("INSERT INTO settings (key, value) VALUES ('voices.source_priority', ?)",
               (json.dumps(["banri:CO4K", "alloha", "vk", "banri:NBY"]),))
    db.execute("INSERT INTO series (id, name, name_en, site, save_path, auto_scan_enabled) VALUES (40, 's', 's', 'x', '/', 0)")
    db.execute("INSERT INTO series_sources (id, series_id, url, source_type, players_off) VALUES (59, 40, 'app://banri/219017',"
               " 'web_video', ?)", (json.dumps(["banri:NBY", "kodik"]),))
    db.execute("INSERT INTO output_settings (series_id, season, lever, value) VALUES (40, 1, 'reference.order', ?)",
               (json.dumps([{"source_id": 59, "player": "banri:CO4K", "group": "Китайский"}]),))
    data = {"video": {"id": {"source_id": 59, "row_key": "banri:CO4K:330"},
                      "snapshot": {"source": "banri", "player": "banri:CO4K"}},
            "tracks": [{"aligned_to": {"source_id": 59, "row_key": "banri:CO4K:330"}, "snapshot": {"player": "vk"}}]}
    db.execute("INSERT INTO plan_assets (series_id, name, data) VALUES (40, 'e330.mkv', ?)", (json.dumps(data),))
    db.execute("INSERT INTO release_tables (url, site, state) VALUES ('app://banri/219017', 'banri', 'ready')")
    db.execute("INSERT INTO release_rows (url, seq, key, player, state) VALUES ('app://banri/219017', 1, 'banri:CO4K:330',"
               " 'banri:CO4K', 'playable')")
    db.execute("INSERT INTO release_rows (url, seq, key, player, state) VALUES ('app://banri/219017', 2, 'vk:1', 'vk', 'playable')")
    db.execute("INSERT INTO composition_rows (series_id, source_id, row_key, seq, player, state) VALUES (40, 59, 'banri:CO4K:330',"
               " 1, 'banri:CO4K', 'playable')")
    db.commit()

    with create_engine(f"sqlite:///{path}").begin() as bind:
        migration().rename_players(bind)

    assert json.loads(db.execute("SELECT value FROM settings WHERE key='voices.source_priority'").fetchone()[0]) == \
        ["line:CO4K", "alloha", "vk", "line:NBY"]
    assert json.loads(db.execute("SELECT players_off FROM series_sources").fetchone()[0]) == ["line:NBY", "kodik"]
    assert json.loads(db.execute("SELECT value FROM output_settings").fetchone()[0])[0]["player"] == "line:CO4K"
    kept = json.loads(db.execute("SELECT data FROM plan_assets").fetchone()[0])
    assert kept["video"]["snapshot"]["player"] == "line:CO4K" and kept["video"]["id"]["row_key"] == "banri:CO4K:330"
    assert kept["tracks"][0]["aligned_to"]["row_key"] == "banri:CO4K:330" and kept["tracks"][0]["snapshot"]["player"] == "vk"
    assert db.execute("SELECT key, player FROM release_rows ORDER BY seq").fetchall() == [("banri:CO4K:330", "line:CO4K"),
                                                                                       ("vk:1", "vk")]
    assert db.execute("SELECT row_key, player FROM composition_rows").fetchall() == [("banri:CO4K:330", "line:CO4K")]
