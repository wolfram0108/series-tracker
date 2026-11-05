"""sources: a row of a table of a page says which video it is, as its host numbers it (`release_rows.video`).

Two rows of one player with one video are one video whatever page shows them; empty for a link that names a place on
its page. The rows that exist get theirs at the next read of their page.
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE release_rows ADD COLUMN video TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE release_rows DROP COLUMN video")
