"""The migration 0081 (the season and the episode of a row as properties; no text made of the numbers), proved before it is run
on production; the test goes with the commit that runs it there."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from modules.rules.engine import compile_profile, process_title
from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent
NUMBER, WS = {"type": "number"}, {"type": "whitespace"}


def text(value):
    return {"type": "text", "value": value}


ROWS = [  # key, player, text, group, site fields, voice kind
    ("cvh:1", "cvh", "18 серия", "4 сезон", {"season": 4, "episode": "18", "vk_id": "v"}, "Многоголосый"),
    ("alloha:1", "alloha", "7 серия", "", {"video_id": 1, "number": "7"}, "Озвучка"),
    ("kodik:1", "kodik", "413-414 серия", "1 сезон", {"value": "414", "translation_id": "1"}, ""),
    ("kodik:2", "kodik", "3 серия", "1 сезон", {"value": "3", "translation_id": "1"}, ""),
    ("allvideo:1", "allvideo", "Серия 5", "", {"id": 1, "number": 5, "compilation_from": None}, ""),
    ("torrent:r/a.mkv#1", "torrent:r", "S:1 | E:2 | TG:DIR | Show/a.mkv", "", {}, "audio"),
    ("vibix:1", "vibix", "Серия 1", "Сезон 1", {"season_index": 1, "position": 1}, ""),
]


def migrate(path):
    spec = importlib.util.spec_from_file_location("m0081", ROOT / "alembic" / "versions" / "0081_rows_season_episode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def rules_of(db, profile_id):
    found = []
    for rid, name, prio, ap, cont, rt in db.execute("SELECT id, name, priority, action_pattern, continue_after_match, rule_table "
                                                     "FROM parser_rules WHERE profile_id=? ORDER BY priority", (profile_id,)):
        conds = [dict(zip(("id", "condition_type", "pattern", "logical_operator"), c)) for c in db.execute(
            "SELECT id, condition_type, pattern, logical_operator FROM parser_rule_conditions WHERE rule_id=? ORDER BY id", (rid,))]
        found.append({"id": rid, "name": name, "priority": prio, "action_pattern": ap, "continue_after_match": bool(cont),
                      "rule_table": json.loads(rt) if rt else None, "conditions": conds})
    return compile_profile(profile_id, found)


def test_a_row_gets_its_season_and_episode_from_its_site_fields_the_made_text_goes_and_the_rules_answer_as_before(tmp_path):
    path = migrated(tmp_path / "m.db")
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE release_rows DROP COLUMN season")
    db.execute("ALTER TABLE release_rows DROP COLUMN episode")
    db.execute("INSERT INTO release_tables (url, site, state) VALUES ('u', 's', 'ready')")
    for seq, (key, player, row_text, group, fields, kind) in enumerate(ROWS):
        db.execute('INSERT INTO release_rows (url, seq, "key", player, grp, text, voice_kind, state, site_fields) '
                   "VALUES ('u', ?, ?, ?, ?, ?, ?, 'playable', ?)", (seq, key, player, group, row_text, kind, json.dumps(fields)))
    db.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
    table = {"action_type": "extract_single", "rows": [{"if": [text("PID")], "then": [WS, NUMBER, WS, text("серия")]}]}
    db.execute("INSERT INTO parser_rules (id, profile_id, name, priority, action_pattern, continue_after_match, rule_table) "
               "VALUES (1, 1, 'Серия', 1, '[]', 0, ?)", (json.dumps(table, ensure_ascii=False),))
    simple = [{"action_type": "extract_single", "action_pattern": json.dumps([NUMBER, {"type": "add", "value": "12"}, WS,
                                                                                text("серия")], ensure_ascii=False)}]
    db.execute("INSERT INTO parser_rules (id, profile_id, name, priority, action_pattern, continue_after_match) "
               "VALUES (2, 2, 'Серия', 1, ?, 0)", (json.dumps(simple, ensure_ascii=False),))
    db.execute("INSERT INTO parser_profiles (id, name) VALUES (2, 'q')")
    voices = [{"action_type": "assign_voiceover", "action_pattern": "AniStar"}]
    db.execute("INSERT INTO parser_rules (id, profile_id, name, priority, action_pattern, continue_after_match) "
               "VALUES (3, 1, 'Озвучка', 0, ?, 1)", (json.dumps(voices),))
    for at, (pattern, operator) in enumerate(((json.dumps([text("V:Netflix")]), "OR"),
                                              (json.dumps([text("сезон | VT:Многоголосый")], ensure_ascii=False), "AND"))):
        db.execute("INSERT INTO parser_rule_conditions (rule_id, condition_type, pattern, logical_operator) VALUES (3, 'contains', ?, ?)",
                   (pattern, operator))
    db.commit()
    lines_before = {key: f"PID:s | {row_text} | G:{group} | VT:{kind}" if group else f"PID:s | {row_text} | VT:{kind}"
                    for key, _p, row_text, group, _f, kind in ROWS}
    profile_before = rules_of(db, 1)
    db.close()

    migrate(path)

    db = sqlite3.connect(path)
    got = {key: (season, episode, row_text, group) for key, season, episode, row_text, group in
           db.execute('SELECT "key", season, episode, text, grp FROM release_rows ORDER BY seq')}
    assert got == {
        "cvh:1": ("4", "18", "", ""),  # the made text and group go
        "alloha:1": ("", "7", "", ""),
        "kodik:1": ("", "414", "413-414 серия", "1 сезон"),  # the player's own text stays
        "kodik:2": ("", "3", "3 серия", "1 сезон"),
        "allvideo:1": ("", "5", "Серия 5", ""),
        "torrent:r/a.mkv#1": ("1", "2", "Show/a.mkv", ""),
        "vibix:1": ("", "", "Серия 1", "Сезон 1")}
    # The constructor answers every row as before: a made text is read from E: now, a text of the player as it was.
    profile_after = rules_of(db, 1)
    for key, season, episode, row_text, group in db.execute('SELECT "key", season, episode, text, grp FROM release_rows'):
        kind = next(each[5] for each in ROWS if each[0] == key)
        fields = [f"PID:s", f"S:{season}" if season else "", f"E:{episode}" if episode else "", row_text,
                  f"G:{group}" if group else "", f"VT:{kind}" if kind else ""]
        after = " | ".join(each for each in fields if each)
        before_answer, after_answer = (process_title(profile_before, lines_before[key]), process_title(profile_after, after))
        assert before_answer["extracted"] == after_answer["extracted"], (key, lines_before[key], after)
    # The modifier of the number goes with it to E:.
    actions = json.loads(db.execute("SELECT action_pattern FROM parser_rules WHERE id = 2").fetchone()[0])
    assert [json.loads(each["action_pattern"])[:3] for each in actions[1:]] == [
        [text("E:"), NUMBER, {"type": "add", "value": "12"}]] * 2
