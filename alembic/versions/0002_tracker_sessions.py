"""tracker_sessions — персистентные сессии трекеров (решение Р-4).

Куки переживают рестарт, чтобы не логиниться на трекеры лишний раз:
частые логины подозрительны для аккаунта и создают ненужную нагрузку
на чужой сервис («мы хотим уважать других»). Владелец таблицы —
модуль trackerauth; механизм инвалидации: проверка живости при первом
использовании + детект протухания с релогином (принцип 7 соблюдён).
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tracker_sessions (
            service        VARCHAR(50) NOT NULL,
            domain         VARCHAR(255) NOT NULL,
            cookies_json   TEXT NOT NULL,
            last_login_at  VARCHAR(32),
            updated_at     VARCHAR(32) NOT NULL,
            PRIMARY KEY (service, domain)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE tracker_sessions")
