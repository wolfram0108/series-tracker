"""planner: the outcome of the last sync of every video series — the work that did not come off for the moment (a crash of
the service, a download cut short), with its reason. It is no failure: the next sync tries again; it is what the window and
the card of the series say until then.
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE plan_outcomes (series_id INTEGER PRIMARY KEY, data TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE plan_outcomes")
