"""catalog: a source of a series keeps its colour (`series_sources.color`, 0–15 of the palette of the interface).

The colour is given once, when the source comes, and stays with it: a source that leaves frees its colour, the others keep
theirs. The sources there are get theirs in the order of their series: the first 0, the next 1, and so on.
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN color INTEGER NOT NULL DEFAULT 0")
    op.execute("UPDATE series_sources SET color = (SELECT (place - 1) % 16 FROM ("
               "SELECT id, ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY position, id) AS place FROM series_sources"
               ") AS ranked WHERE ranked.id = series_sources.id)")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources DROP COLUMN color")
