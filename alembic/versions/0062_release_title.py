"""sources: the name a site gives the source of a page, for the head of a window (ReleaseTable.title).

A card of a video application is named by its site (`半日 · 万界独尊 (2021)`); a page of any other site has none and the
window names it by the place of its address. An empty title is the value of every table read before.
"""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE release_tables ADD COLUMN title TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE release_tables DROP COLUMN title")
