"""rules: a rule of the kind «Таблица» keeps its rows in the rule itself.

`rule_table` holds the action of the column «ТО» and the rows in their order (JSON: {action_type, rows: [{if, then}]});
a rule with it is a table, a rule without it an ordinary one. The rules there are have none and do not change.
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_rules ADD COLUMN rule_table TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_rules DROP COLUMN rule_table")
