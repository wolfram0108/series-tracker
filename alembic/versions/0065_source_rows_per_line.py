"""series_sources: how many rows of each line of a card an application source reads.

`all` reads the whole line, `N` the first N rows, `-N` the last N (core/row_limit.py). Every source stored so far reads
everything, as it did.
"""
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN rows_per_line TEXT NOT NULL DEFAULT 'all'")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources DROP COLUMN rows_per_line")
