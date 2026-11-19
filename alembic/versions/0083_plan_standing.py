"""planner: what every series stood at when its view was last counted (ready, waiting, error), as the card of the series shows
it. A start tells it at once, before the full recount of the views has gone over every series; the recount then puts right
what it counts otherwise.
"""
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE plan_standing (series_id INTEGER PRIMARY KEY, flags TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE plan_standing")
