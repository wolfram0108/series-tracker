"""downloads: the cards of the downloads that stay after their sync.

A failed card, and a finished one whose plan was rebuilt, stay once the sync of their series has ended, until the user
removes them or the next sync of the series begins (core/contracts/downloads.py). `data` is the card (JSON).
"""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE download_cards (id TEXT PRIMARY KEY, series_id INTEGER NOT NULL, data TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE download_cards")
