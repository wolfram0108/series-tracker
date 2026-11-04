"""composition: a row the source's own choice leaves out is kept in the layout as excluded, `exclusion='source'`.

A switched-off player, or another season of a catalog, is a choice of the user, and the layout now says so: the video is
there and is not wanted. Without the row it would look like a row a read did not bring, which proves nothing, so what
was made from it would be kept. SQLite cannot widen a CHECK in place, so the table is built again with the same columns.

The downgrade takes these rows away: the earlier schema has no word for them and its layout never held them.
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_COLUMNS = ("series_id, source_id, row_key, seq, excluded, exclusion, season, episode_start, episode_end, part, voice_group, "
            "voice_label, voice_name, voice_unknown, player, player_label, grp, text, heights, state, published, link")


def _table(name: str, exclusions: str) -> str:
    return (
        f"CREATE TABLE {name} ("
        " series_id INTEGER NOT NULL,"
        " source_id INTEGER NOT NULL,"
        " row_key TEXT NOT NULL,"
        " seq INTEGER NOT NULL,"
        " excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),"
        f" exclusion TEXT CHECK (exclusion IN ({exclusions})),"
        " season INTEGER,"
        " episode_start INTEGER,"
        " episode_end INTEGER,"
        " part INTEGER,"
        " voice_group TEXT,"
        " voice_label TEXT NOT NULL DEFAULT '',"
        " voice_name TEXT NOT NULL DEFAULT '',"
        " voice_unknown INTEGER,"
        " player TEXT NOT NULL,"
        " player_label TEXT NOT NULL DEFAULT '',"
        " grp TEXT NOT NULL DEFAULT '',"
        " text TEXT NOT NULL DEFAULT '',"
        " heights TEXT NOT NULL DEFAULT '[]',"
        " state TEXT NOT NULL CHECK (state IN ('playable', 'absent', 'not_obtained')),"
        " published TEXT,"
        " link TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (series_id, source_id, row_key),"
        " UNIQUE (series_id, seq))")


def _rebuild(exclusions: str, keep: str) -> None:
    op.execute(_table("composition_rows_next", exclusions))
    op.execute(f"INSERT INTO composition_rows_next ({_COLUMNS}) SELECT {_COLUMNS} FROM composition_rows WHERE {keep}")
    op.execute("DROP TABLE composition_rows")
    op.execute("ALTER TABLE composition_rows_next RENAME TO composition_rows")


def upgrade() -> None:
    _rebuild("'rule', 'no_number', 'source'", "1")


def downgrade() -> None:
    _rebuild("'rule', 'no_number'", "exclusion IS NULL OR exclusion != 'source'")
