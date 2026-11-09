"""catalog: the markup reference of a series (stage 14).

`series_sources.markup` marks the source whose episodes give the timeline of every video of the series. At most one
source of a series is it: the partial unique index holds that, and the repository unmarks the others in the
transaction that marks one.
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN markup INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE UNIQUE INDEX ux_series_sources_markup ON series_sources (series_id) WHERE markup = 1")


def downgrade() -> None:
    op.execute("DROP INDEX ux_series_sources_markup")
    op.execute("ALTER TABLE series_sources DROP COLUMN markup")
