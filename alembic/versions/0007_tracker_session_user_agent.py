"""tracker_session_user_agent — User-Agent прогретой сессии трекера.

Зачем колонка. Трекер за антибот-защитой (Cloudflare) пускает только
после челленджа; пройденный челлендж подтверждается кукой `cf_clearance`,
и эта кука действительна ТОЛЬКО в паре с тем User-Agent, которым её
получили. Значит UA перестал быть константой кода и стал частью
состояния сессии — его нужно хранить рядом с куками и восстанавливать
при старте вместе с ними, иначе после рестарта сохранённая кука
бесполезна и челлендж придётся проходить заново.

Владелец таблицы — модуль trackerauth (Р-7). NULL = сессия обычная,
UA берётся из провайдера (kinozal, VK и прочие без челленджа).
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tracker_sessions ADD COLUMN user_agent TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tracker_sessions DROP COLUMN user_agent")
