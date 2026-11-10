"""planner: the cards of the packing that stay after their sync.

A failed card of the packing of a container stays once the sync of its series has ended, until the user removes it or the
next sync of the series begins (core/contracts/packing.py). `data` is the card (JSON).
"""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE pack_cards (id TEXT PRIMARY KEY, series_id INTEGER NOT NULL, data TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE pack_cards")
