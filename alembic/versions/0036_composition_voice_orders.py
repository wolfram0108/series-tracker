"""composition: the order of the voice-overs of each season of a series (ЭТАП_11).

`voice_orders.labels` is the list of voice-over groups in the order their tracks take in the container of every episode
of the season, the first being the default track. It is kept in the terms of the domain (the names of the groups), so
that it outlives a new reading of a table; a group that has no row now keeps its place for when it comes back.
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE voice_orders ("
        " series_id INTEGER NOT NULL,"
        " season INTEGER NOT NULL,"
        " labels TEXT NOT NULL DEFAULT '[]',"
        " PRIMARY KEY (series_id, season))")


def downgrade() -> None:
    op.execute("DROP TABLE voice_orders")
