"""media_items.download_url — фактический URL загрузки.

Для VK каноническая ссылка `vk.com/video...` может отдавать
«Access restricted», хотя embed-страница с hash содержит рабочие mp4-URL.
Кандидат хранит канонический source_url (идентичность) и отдельный
download_url (embed-ссылка), который разрешается в поток перед загрузкой.
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_items ADD COLUMN download_url TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE media_items DROP COLUMN download_url")
