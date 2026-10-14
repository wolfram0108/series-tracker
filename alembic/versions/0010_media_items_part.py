"""media_items_part — поддержка частей серий (part).

Колонка part хранит номер части составного эпизода (например, s01e08 Part 1).
NULL означает обычную серию без разделения на части.
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_items ADD COLUMN part INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE media_items DROP COLUMN part")
