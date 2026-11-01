"""composition: the layout of a series, its sources, the numbers of unknown voices and the output settings.

`composition_rows` holds the layout in the order the projection gave it (`seq`); `composition_state` the revision of
the last published layout; `composition_sources` what the window shows about each source and the canonical address of
its page, by which the events of `sources` find it; `composition_failures` the verdict of the last failed read of a page;
`unknown_voices` the number of a voice-over that is in no group; `output_settings` the values the user set, in the terms
of the domain, so that they outlive a new reading of a table (NULL season or episode is a level above).
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE composition_state ("
        " series_id INTEGER PRIMARY KEY,"
        " rev INTEGER NOT NULL DEFAULT 0)")
    op.execute(
        "CREATE TABLE composition_sources ("
        " source_id INTEGER PRIMARY KEY,"
        " series_id INTEGER NOT NULL,"
        " position INTEGER NOT NULL DEFAULT 0,"
        " table_url TEXT,"
        " summary TEXT NOT NULL)")
    op.execute("CREATE INDEX composition_sources_series ON composition_sources (series_id)")
    op.execute("CREATE INDEX composition_sources_url ON composition_sources (table_url)")
    op.execute(
        "CREATE TABLE composition_failures ("
        " table_url TEXT PRIMARY KEY,"
        " reason TEXT NOT NULL)")
    op.execute(
        "CREATE TABLE composition_rows ("
        " series_id INTEGER NOT NULL,"
        " source_id INTEGER NOT NULL,"
        " row_key TEXT NOT NULL,"
        " seq INTEGER NOT NULL,"
        " excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),"
        " exclusion TEXT CHECK (exclusion IN ('rule', 'no_number')),"
        " season INTEGER,"
        " episode_start INTEGER,"
        " episode_end INTEGER,"
        " part INTEGER,"
        " voice_group TEXT,"
        " voice_label TEXT NOT NULL DEFAULT '',"
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
    op.execute(
        "CREATE TABLE unknown_voices ("
        " series_id INTEGER NOT NULL,"
        " name_key TEXT NOT NULL,"
        " number INTEGER NOT NULL,"
        " PRIMARY KEY (series_id, name_key))")
    op.execute(
        "CREATE TABLE output_settings ("
        " series_id INTEGER NOT NULL,"
        " season INTEGER,"
        " episode INTEGER,"
        " lever TEXT NOT NULL,"
        " target TEXT,"
        " value TEXT NOT NULL,"
        " CHECK (episode IS NULL OR season IS NOT NULL))")
    # NULL is not equal to NULL in a unique key, so the levels without a season or an episode are keyed by a stand-in.
    op.execute(
        "CREATE UNIQUE INDEX output_settings_slot ON output_settings"
        " (series_id, IFNULL(season, -1), IFNULL(episode, -1), lever, IFNULL(target, ''))")


def downgrade() -> None:
    op.execute("DROP INDEX output_settings_slot")
    op.execute("DROP TABLE output_settings")
    op.execute("DROP TABLE unknown_voices")
    op.execute("DROP TABLE composition_rows")
    op.execute("DROP TABLE composition_failures")
    op.execute("DROP INDEX composition_sources_url")
    op.execute("DROP INDEX composition_sources_series")
    op.execute("DROP TABLE composition_sources")
    op.execute("DROP TABLE composition_state")
