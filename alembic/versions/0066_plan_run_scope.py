"""plan_runs: what a sync under way covers.

A sync of a season or of an episode writes its scope with its run (JSON `{season, episode?}`), so that a restart takes it
up again as it was, not as a sync of the whole series. A run written before has none: it covers the whole series.
"""
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE plan_runs ADD COLUMN scope TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE plan_runs DROP COLUMN scope")
