"""The season and the episode of a row are properties of their own, raw as the player sends them; the text of a row is only what
the player gives. Two parsers made a text of the numbers (CDN VideoHub «{episode} серия», «{season} сезон»; the own videos of
yummyani «{number} серия»): that text goes, and the rules of the constructor that read a number from it read it from `E:` too.

The rows kept get their season and episode from the fields of the site they were read with (nothing is read again): CDN VideoHub
its `season` and `episode`, yummyani its `number`, Kodik its `value`, ruchime its `number`; a row of a torrent from the text the
editor gave it (`S:… | E:… | <video>`). A template of an extraction «число серия» gets, next to it, the templates «E:число» with
the same number (its modifier too) followed by the next field or the end of the line: in a table, rows after it with the same
«ЕСЛИ»; in a rule, actions after it. The row it was written for still answers to the first; a row whose text is gone answers to
the second, and a row whose text stays (Kodik) has that text after its `E:`, which the second does not take. A template that read
the word of the made-up group with the field after it («сезон | VT:…») gets «E:число | VT:…» as its alternative."""
import json
import re

from alembic import op
from sqlalchemy import text

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

_TORRENT = re.compile(r"^S:(\S+) \| E:(\S+) \| (?:TG:\S+ \| )?(.*)$", re.DOTALL)
_ATTACHED = ("add", "subtract", "range")


def row_fields(player: str, fields: dict, row_text: str, group: str) -> tuple[str, str, str, str]:
    """(season, episode, text, group) of a kept row."""
    season = episode = ""
    if player == "cvh":
        season = "" if fields.get("season") is None else str(fields["season"])
        episode = str(fields.get("episode") or "")
        if episode and row_text == f"{episode} серия":
            row_text = ""
        if season and group == f"{season} сезон":
            group = ""
    elif "video_id" in fields and "number" in fields:  # an own video of yummyani
        episode = str(fields.get("number") or "")
        if episode and row_text == f"{episode} серия":
            row_text = ""
    elif player == "kodik":
        episode = str(fields.get("value") or "")
    elif "compilation_from" in fields:  # an entry of ruchime
        episode = "" if fields.get("number") is None else str(fields["number"])
    elif player.startswith("torrent:"):
        found = _TORRENT.match(row_text)
        if found:
            season, episode, row_text = found.group(1), found.group(2), found.group(3)
    return season, episode, row_text, group


# A row whose text is gone has, after its episode, the next field (a code) or nothing: a row of a player whose text stays (Kodik,
# ruchime, a torrent) has its text there, and keeps the answer the template written for that text gives.
_NEXT_FIELD = [{"type": "text", "value": " | G:"}, {"type": "or"}, {"type": "text", "value": " | V:"}, {"type": "or"},
               {"type": "text", "value": " | VT:"}, {"type": "or"}, {"type": "text", "value": " | R:"}]


def _number_at(blocks: list, at: int) -> list[dict]:
    """The number block at `at` with what hangs on it."""
    end = at + 1
    while end < len(blocks) and isinstance(blocks[end], dict) and blocks[end].get("type") in _ATTACHED:
        end += 1
    return [dict(each) for each in blocks[at:end]]


def episode_alternatives(blocks) -> list[list[dict]]:
    """For a template that takes the number before the word «серия»: «E:число» followed by the next field, and «E:число» at
    the end of the line (the number with what hangs on it); none for any other template."""
    if not isinstance(blocks, list):
        return []
    for at, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "number":
            continue
        number = _number_at(blocks, at)
        rest = at + len(number)
        while rest < len(blocks) and isinstance(blocks[rest], dict) and blocks[rest].get("type") == "whitespace":
            rest += 1
        if (rest < len(blocks) and isinstance(blocks[rest], dict) and blocks[rest].get("type") == "text"
                and str(blocks[rest].get("value", "")).strip().lower().startswith("серия")):
            head = [{"type": "text", "value": "E:"}] + number
            return [head + [dict(each) for each in _NEXT_FIELD], head + [{"type": "end_of_line"}]]
    return []


def season_alternative(blocks) -> list[dict] | None:
    """For a template that reads the word «сезон» of a made-up group with the field after it («сезон | VT:…»): the episode
    of the row with that field after it («E:число | VT:…»); None for any other template."""
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        value = str(block.get("value", "")) if isinstance(block, dict) and block.get("type") == "text" else ""
        if value.lower().startswith("сезон |"):
            return [{"type": "text", "value": "E:"}, {"type": "number"}, {"type": "text", "value": value[len("сезон"):]}]
    return None


def table_with_alternatives(table: dict) -> dict | None:
    """The table with, after a row of an extraction of «число серия», its rows «E:число»; after a row whose «ЕСЛИ» reads
    «сезон | …», its row «E:число | …»; None when it has none."""
    rows, changed = [], False
    for row in table.get("rows") or []:
        rows.append(row)
        own = {"action_type": row["action_type"]} if row.get("action_type") else {}
        kind = row.get("action_type") or table.get("action_type")
        for alternative in (episode_alternatives(row.get("then")) if kind == "extract_single" else []):
            rows.append({"if": row.get("if"), "then": alternative, "if_joined": True, **own})
            changed = True
        season = season_alternative(row.get("if"))
        if season is not None:
            rows.append({"if": season, "then": row.get("then"), **own})
            changed = True
    return {**table, "rows": rows} if changed else None


def actions_with_alternatives(actions: list) -> list | None:
    """The actions of a rule with, after an extraction of «число серия», its extractions «E:число»; None when none."""
    out, changed = [], False
    for action in actions:
        out.append(action)
        if action.get("action_type") == "extract_single":
            try:
                blocks = json.loads(action.get("action_pattern") or "[]")
            except ValueError:
                continue
            for alternative in episode_alternatives(blocks):
                out.append({"action_type": "extract_single", "action_pattern": json.dumps(alternative, ensure_ascii=False)})
                changed = True
    return out if changed else None


def upgrade() -> None:
    op.execute("ALTER TABLE release_rows ADD COLUMN season TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE release_rows ADD COLUMN episode TEXT NOT NULL DEFAULT ''")
    conn = op.get_bind()
    rows = conn.execute(text('SELECT url, "key", player, site_fields, text, grp FROM release_rows')).fetchall()
    for url, key, player, site_fields, row_text, group in rows:
        try:
            fields = json.loads(site_fields or "{}")
        except ValueError:
            fields = {}
        made = row_fields(player, fields if isinstance(fields, dict) else {}, row_text, group)
        if made != ("", "", row_text, group):
            conn.execute(text('UPDATE release_rows SET season = :s, episode = :e, text = :t, grp = :g '
                              'WHERE url = :u AND "key" = :k'),
                         {"s": made[0], "e": made[1], "t": made[2], "g": made[3], "u": url, "k": key})
    for rule_id, action_pattern, rule_table in conn.execute(
            text("SELECT id, action_pattern, rule_table FROM parser_rules")).fetchall():
        if rule_table:
            table = table_with_alternatives(json.loads(rule_table))
            if table is not None:
                conn.execute(text("UPDATE parser_rules SET rule_table = :t WHERE id = :i"),
                             {"t": json.dumps(table, ensure_ascii=False), "i": rule_id})
            continue
        try:
            actions = json.loads(action_pattern or "[]")
        except ValueError:
            continue
        made = actions_with_alternatives(actions) if isinstance(actions, list) else None
        if made is not None:
            conn.execute(text("UPDATE parser_rules SET action_pattern = :a WHERE id = :i"),
                         {"a": json.dumps(made, ensure_ascii=False), "i": rule_id})
    # A condition that reads «сезон | …» gets the condition «E:число | …» as one more alternative (OR), at the end; the last
    # condition before it closes its group (its operator did nothing, being the last).
    for (rule_id,) in conn.execute(text("SELECT DISTINCT rule_id FROM parser_rule_conditions")).fetchall():
        conditions = conn.execute(text("SELECT id, condition_type, pattern, logical_operator FROM parser_rule_conditions "
                                       "WHERE rule_id = :r ORDER BY id"), {"r": rule_id}).fetchall()
        added = []
        for _id, kind, pattern, _operator in conditions:
            try:
                season = season_alternative(json.loads(pattern or "[]")) if kind == "contains" else None
            except ValueError:
                season = None
            if season is not None:
                added.append(season)
        if added:
            conn.execute(text("UPDATE parser_rule_conditions SET logical_operator = 'OR' WHERE id = :i"), {"i": conditions[-1][0]})
            for at, season in enumerate(added):
                conn.execute(text("INSERT INTO parser_rule_conditions (rule_id, condition_type, pattern, logical_operator) "
                                  "VALUES (:r, 'contains', :p, :o)"),
                             {"r": rule_id, "p": json.dumps(season, ensure_ascii=False), "o": "OR" if at < len(added) - 1 else "AND"})


def downgrade() -> None:
    raise NotImplementedError("the text made of the numbers is not made again")
