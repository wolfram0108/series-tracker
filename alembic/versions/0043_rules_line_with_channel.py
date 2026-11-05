"""rules: a text block that ran from `PID:vkvideo.ru | ` into the next field no longer meets the line of a VK channel.

The channel stands between the host and the player now (`PID:vkvideo.ru | mir.donghua | P:VK | …`). A text block of a
condition or of an action that read `P:VK` right after the host is parted into the host, any text, and the rest, so the rule
keeps meaning what it meant for every channel and for a table read before. On the way back nothing is undone: the parted
blocks meet the lines without a channel as well.
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

HOST = "PID:vkvideo.ru | "


def _parted(blocks: list) -> tuple[list, bool]:
    """The blocks with each text that runs past the host of VK parted at it; whether any was."""
    out, changed = [], False
    for block in blocks:
        value = block.get("value") if isinstance(block, dict) and block.get("type") == "text" else None
        at = value.find(HOST) if isinstance(value, str) else -1
        if at < 0 or not value[at + len(HOST):]:
            out.append(block)
            continue
        head, rest = value[:at + len(HOST)], value[at + len(HOST):]
        out += [{"type": "text", "value": head}, {"type": "any_text"}, {"type": "text", "value": rest}]
        changed = True
    return out, changed


def _blocks(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) and all(isinstance(each, dict) and "type" in each for each in value) else None


def _actions(raw):
    """The actions of a rule: a list of objects, each with the blocks of its pattern as a JSON string (or plain words)."""
    try:
        value = json.loads(raw or "")
    except ValueError:
        return None
    return value if isinstance(value, list) and all(isinstance(each, dict) for each in value) else None


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def upgrade() -> None:
    conn = op.get_bind()
    for condition_id, pattern in conn.execute(text("SELECT id, pattern FROM parser_rule_conditions")).fetchall():
        blocks = _blocks(pattern)
        if blocks is None:
            continue
        parted, changed = _parted(blocks)
        if changed:
            conn.execute(text("UPDATE parser_rule_conditions SET pattern = :p WHERE id = :i"),
                         {"p": _dump(parted), "i": condition_id})
    for rule_id, raw in conn.execute(text("SELECT id, action_pattern FROM parser_rules")).fetchall():
        actions = _actions(raw)
        if actions is None:
            continue
        changed = False
        for action in actions:
            blocks = _blocks(action.get("action_pattern"))
            if blocks is None:
                continue
            parted, moved = _parted(blocks)
            if moved:
                action["action_pattern"] = _dump(parted)
                changed = True
        if changed:
            conn.execute(text("UPDATE parser_rules SET action_pattern = :p WHERE id = :i"),
                         {"p": _dump(actions), "i": rule_id})


def downgrade() -> None:
    pass
