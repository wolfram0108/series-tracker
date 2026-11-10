"""sources: a line of the video applications is one player whichever application lists it (`line:<code>`).

The player of a row of a line of an application was `<application>:<code>` (`banri:CO4K`); it is `line:<code>`. Every
name of such a player kept by the user or by the work of a sync is renamed: the order of sources, the players switched off
in a source, the order of a queue of the window «Выход», the provenance of the files of a series, the rows of the tables
and of the layout. The keys of the rows (`banri:CO4K:330`) stay: they name the row of the source, not the player.
"""
from __future__ import annotations

import json
import re

from alembic import op
from sqlalchemy import text

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

# The applications whose lines were players of their own before (the profiles of the environment on the day of it).
APPS = ("banri",)
_NAME = re.compile(rf"^(?:{'|'.join(APPS)}):([^:]+)$")


def renamed(name):
    """The new name of a player of a line; any other value as it is."""
    found = _NAME.match(name) if isinstance(name, str) else None
    return f"line:{found.group(1)}" if found else name


def _players_in(value):
    """Every value under a key `player` renamed, at any depth of a JSON value."""
    if isinstance(value, dict):
        return {key: renamed(each) if key == "player" else _players_in(each) for key, each in value.items()}
    if isinstance(value, list):
        return [_players_in(each) for each in value]
    return value


def rename_players(bind) -> None:
    for key, value in bind.execute(text("SELECT key, value FROM settings WHERE key = 'voices.source_priority'")).fetchall():
        bind.execute(text("UPDATE settings SET value = :value WHERE key = :key"),
                     {"key": key, "value": json.dumps([renamed(each) for each in json.loads(value)], ensure_ascii=False)})
    for row_id, value in bind.execute(text("SELECT id, players_off FROM series_sources WHERE players_off LIKE '%:%'")).fetchall():
        bind.execute(text("UPDATE series_sources SET players_off = :value WHERE id = :id"),
                     {"id": row_id, "value": json.dumps([renamed(each) for each in json.loads(value)], ensure_ascii=False)})
    for rowid, value in bind.execute(text("SELECT rowid, value FROM output_settings WHERE value LIKE '%\"player\"%'")).fetchall():
        bind.execute(text("UPDATE output_settings SET value = :value WHERE rowid = :rowid"),
                     {"rowid": rowid, "value": json.dumps(_players_in(json.loads(value)), ensure_ascii=False)})
    for rowid, value in bind.execute(text("SELECT rowid, data FROM plan_assets WHERE data LIKE '%\"player\"%'")).fetchall():
        bind.execute(text("UPDATE plan_assets SET data = :value WHERE rowid = :rowid"),
                     {"rowid": rowid, "value": json.dumps(_players_in(json.loads(value)), ensure_ascii=False)})
    for table in ("release_rows", "composition_rows"):
        for (player,) in bind.execute(text(f"SELECT DISTINCT player FROM {table} WHERE player LIKE '%:%'")).fetchall():
            if renamed(player) != player:
                bind.execute(text(f"UPDATE {table} SET player = :new WHERE player = :old"),
                             {"new": renamed(player), "old": player})


def upgrade() -> None:
    rename_players(op.get_bind())


def downgrade() -> None:
    raise NotImplementedError("the names of the players of the lines before are not kept")
