"""saved_paths — сохранённые пользователем пути загрузки.

Чтобы не вводить путь вручную каждый раз: пользователь заводит набор
путей (Настройки → Отладка), а в формах добавления/правки сериала
выбирает их из выпадающего списка. Владелец таблицы — модуль settings
(конфигурация пользователя). UNIQUE(path) — без дублей.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE saved_paths (
            id    INTEGER NOT NULL,
            path  TEXT NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (path)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE saved_paths")
