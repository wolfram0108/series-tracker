"""catalog: sources of a series.

Filled by its package of stage 10; until then the revision keeps the chain whole.
"""
from alembic import op  # noqa: F401

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
