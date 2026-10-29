"""voice_epoch counts the starts of the voices module.

The interface orders the packets it receives by (epoch, rev): a start of the module is a new epoch, whatever
the clock of the machine says, so the packets of a later run are always the newer ones.
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE voice_epoch (id INTEGER PRIMARY KEY CHECK (id = 1), value INTEGER NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE voice_epoch")
