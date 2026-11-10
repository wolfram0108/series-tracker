"""composition: a row of the layout says whether its source gave it in its last read.

`stale` is 1 for a row its source did not give in its last read (a part of the page that did not answer, or a read that
failed): it stands from an earlier read. Every row there is gets 0 and is told anew at the next recount of its series.
"""
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE composition_rows ADD COLUMN stale INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE composition_rows DROP COLUMN stale")
