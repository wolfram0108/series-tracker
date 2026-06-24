"""admin_user — учётка администратора веб-интерфейса (один аккаунт).

Веб-интерфейс сетевой и может быть открыт из интернета, поэтому вход
обязателен (см. docs/security.md, Этап 1). Владелец таблицы — модуль auth.
Хранится логин и argon2-хэш пароля; открытый пароль не хранится. CHECK
(id = 1) гарантирует единственного администратора (приложение
однопользовательское).
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE admin_user ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "username TEXT NOT NULL, "
        "password_hash TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE admin_user")
