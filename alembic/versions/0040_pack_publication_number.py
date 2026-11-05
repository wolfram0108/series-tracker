"""downloads: the number of a publication of a container, written into the container it builds (`ST_PUBLICATION`).

A publication cut short is finished only when the file under its name is the container it built (its number): the same
video and tracks of another resolution read alike and were taken for it. From this revision the staging name and the
leftovers of a record are names inside the folder of the series, so moving the folder does not make the record stale.
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pack_publications ADD COLUMN publication TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE pack_publications DROP COLUMN publication")
