"""composition: a row of the layout keeps which video it is (`composition_rows.video`), as the table of its page says.

The rows that exist get theirs at the next count of their series.
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE composition_rows ADD COLUMN video TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE composition_rows DROP COLUMN video")
