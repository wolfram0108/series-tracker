"""composition: the user's names of the tracks of the voice-over groups of a series.

`voice_titles.title` is the title the track of the group `label` bears in every container of the series, instead of the
name of the group; the group stays what the track is known by (its tags). Kept in the terms of the domain (the names of
the groups), like the order of the voice-overs (0036). A group with no row here bears its own name.
"""
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE voice_titles ("
        " series_id INTEGER NOT NULL,"
        " label TEXT NOT NULL,"
        " title TEXT NOT NULL,"
        " PRIMARY KEY (series_id, label))")


def downgrade() -> None:
    op.execute("DROP TABLE voice_titles")
