"""composition: a counter of the writes of every series (composition_state.writes), raised by every write, whatever it
changes; planner: the print of the inputs its standing was counted from (plan_standing.inputs). A start counts again only
the series whose inputs are not the ones their standing was counted from.
"""
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE composition_state ADD COLUMN writes INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE plan_standing ADD COLUMN inputs TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE plan_standing DROP COLUMN inputs")
    op.execute("ALTER TABLE composition_state DROP COLUMN writes")
