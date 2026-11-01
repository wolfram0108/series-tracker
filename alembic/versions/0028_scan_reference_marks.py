"""scan: plan rows refer to composition rows, and the reference of every episode is kept with its mark.

`media_items.source_id` and `media_items.row_key` point at the row of the layout a plan row was made from; they stay
NULL on rows of torrents and of earlier versions. `scan_references` holds the outcome of the choice for every slot
(season, episode, part), including the slots that have no plan row because nothing fits (`none`, `unfit`); NULL part is
a slot without a part, keyed by a stand-in as in `output_settings`. `video_availability` goes: the report of players is
the state of the rows of a table now.

downgrade brings the empty report table back; the references and the links of plan rows are lost.
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_VIDEO_AVAILABILITY = """CREATE TABLE video_availability (
    series_id INTEGER NOT NULL,
    report TEXT NOT NULL,
    checked_at DATETIME NOT NULL,
    PRIMARY KEY (series_id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)"""


def upgrade() -> None:
    op.execute("DROP TABLE video_availability")
    op.execute("ALTER TABLE media_items ADD COLUMN source_id INTEGER")
    op.execute("ALTER TABLE media_items ADD COLUMN row_key TEXT")
    op.execute("CREATE INDEX media_items_row ON media_items (series_id, source_id, row_key)")
    op.execute(
        "CREATE TABLE scan_references ("
        " series_id INTEGER NOT NULL,"
        " season INTEGER NOT NULL,"
        " episode INTEGER NOT NULL,"
        " part INTEGER,"
        " mark TEXT NOT NULL CHECK (mark IN ('by_level', 'own', 'other_player', 'none', 'unfit')),"
        " source_id INTEGER,"
        " row_key TEXT,"
        " unique_id TEXT,"
        " player TEXT,"
        " voice_group TEXT,"
        " height INTEGER,"
        " reason TEXT,"
        " FOREIGN KEY(series_id) REFERENCES series (id))")
    op.execute(
        "CREATE UNIQUE INDEX scan_references_slot ON scan_references"
        " (series_id, season, episode, IFNULL(part, -1))")


def downgrade() -> None:
    op.execute("DROP TABLE scan_references")
    op.execute("DROP INDEX media_items_row")
    op.execute("ALTER TABLE media_items DROP COLUMN row_key")
    op.execute("ALTER TABLE media_items DROP COLUMN source_id")
    op.execute(_VIDEO_AVAILABILITY)
