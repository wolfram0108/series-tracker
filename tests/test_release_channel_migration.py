"""Migrations 0042 and 0043: the channel of a page joins its table, and a rule that read `P:VK` right after the host of VK keeps meeting
the lines of VK when the channel stands between them."""
import json
import re
import sqlite3
import subprocess
import sys

from modules.rules.engine import _blocks_to_regex


def alembic(path, *args):
    subprocess.run([sys.executable, "-m", "alembic", *args],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=".", check=True,
                   capture_output=True)


SPANNING = [{"type": "text", "value": "PID:vkvideo.ru | P:VK"}]
HOST_ONLY = [{"type": "text", "value": "PID:vkvideo.ru"}, {"type": "any_text"}, {"type": "number"}]
ACTION = [{"type": "text", "value": "PID:vkvideo.ru | P:VK | "}, {"type": "any_text"}, {"type": "number"}]


def test_the_channel_is_taken_from_the_address_and_a_rule_across_the_host_is_parted_to_keep_its_meaning(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0041")
    with sqlite3.connect(path) as conn:
        for url, site in (("https://vkvideo.ru/@mir.donghua|За гранью времени", "vk_channel"),
                          ("https://1.ruchime.org/release/x", "ruchime")):
            conn.execute("INSERT INTO release_tables (url, site, state) VALUES (?, ?, 'ready')", (url, site))
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        conn.execute("INSERT INTO parser_rules (id, profile_id, name, priority, action_pattern, continue_after_match) "
                     "VALUES (1, 1, 'r', 0, ?, 0)",
                     (json.dumps([{"action_type": "extract_single", "action_pattern": json.dumps(ACTION)},
                                  {"action_type": "assign_voiceover", "action_pattern": "ПВА ШОУ"}]),))
        for number, pattern in ((1, SPANNING), (2, HOST_ONLY)):
            conn.execute("INSERT INTO parser_rule_conditions (id, rule_id, condition_type, pattern, logical_operator) "
                         "VALUES (?, 1, 'contains', ?, 'OR')", (number, json.dumps(pattern)))

    alembic(path, "upgrade", "0043")

    with sqlite3.connect(path) as conn:
        assert dict(conn.execute("SELECT url, channel FROM release_tables")) == {
            "https://vkvideo.ru/@mir.donghua|За гранью времени": "mir.donghua", "https://1.ruchime.org/release/x": ""}
        conditions = dict(conn.execute("SELECT id, pattern FROM parser_rule_conditions"))
        actions = json.loads(conn.execute("SELECT action_pattern FROM parser_rules").fetchone()[0])
    assert json.loads(conditions[2]) == HOST_ONLY  # a rule that does not cross the host is left as it is
    parted = json.loads(conditions[1])
    assert parted == [{"type": "text", "value": "PID:vkvideo.ru | "}, {"type": "any_text"}, {"type": "text", "value": "P:VK"}]
    assert actions[1] == {"action_type": "assign_voiceover", "action_pattern": "ПВА ШОУ"}
    for blocks in (parted, json.loads(actions[0]["action_pattern"])):
        regex = re.compile(_blocks_to_regex(blocks, capture_numbers=False))
        assert regex.search("PID:vkvideo.ru | pvashow | P:VK | Боевой мастер | 706 серия")
        assert regex.search("PID:vkvideo.ru | P:VK | Боевой мастер | 706 серия")  # a table read before keeps its meaning

    alembic(path, "downgrade", "0041")
    with sqlite3.connect(path) as conn:
        assert "channel" not in [row[1] for row in conn.execute("PRAGMA table_info(release_tables)")]
