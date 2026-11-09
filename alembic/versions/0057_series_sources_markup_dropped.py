"""catalog: the mark of the markup reference leaves the source (stage 14).

A source holds many voice-overs and players, so the markup reference is a variant, kept as the levers `markup.order` and
`markup.allow` of the levels of a series (composition). The column of 0056 and its index go.
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_series_sources_markup")
    op.execute("ALTER TABLE series_sources DROP COLUMN markup")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN markup INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE UNIQUE INDEX ux_series_sources_markup ON series_sources (series_id) WHERE markup = 1")
