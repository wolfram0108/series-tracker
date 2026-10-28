"""media_items.superseded_by — победитель плана, вытеснивший строку.

Планировщик (scan) знает, чей файл занимает слот эпизода; downloads удаляет
файлы вытесненных строк только по этому указанию, не повторяя логику слота.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_items ADD COLUMN superseded_by TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE media_items DROP COLUMN superseded_by")
