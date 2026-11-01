"""scan: plan rows refer to composition rows.

Filled by its package of stage 10; until then the revision keeps the chain whole.
"""
from alembic import op  # noqa: F401

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
