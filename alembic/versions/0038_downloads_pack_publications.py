"""downloads: the journal of the publications of containers (ЭТАП_11, И-61).

A container is built aside and put under its name in one replace; the old names of the rows it displaced and the files
of the tracks it took in are dropped after that. A row of `pack_publications` is written before the build and holds
everything the publication must still do (the name the winner takes, the rows it displaces, the tracks voices is to
record as packed, the files to remove), so that a stop at any step is finished at the next packing of the series and no
file is left without an owner. The table belongs to downloads.
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pack_publications (
            id INTEGER PRIMARY KEY,
            series_id INTEGER NOT NULL,
            unique_id TEXT NOT NULL,
            name TEXT NOT NULL,
            staging TEXT NOT NULL,
            identity TEXT NOT NULL,
            signature TEXT NOT NULL,
            losers TEXT NOT NULL,
            leftovers TEXT NOT NULL,
            dub_ids TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
    op.execute("CREATE INDEX ix_pack_publications_series ON pack_publications (series_id)")


def downgrade() -> None:
    op.execute("DROP TABLE pack_publications")
