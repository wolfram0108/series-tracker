"""video_availability — последний отчёт о доступности источников web-видео.

Одна строка на серию: какие плееры есть, какие дали поток, с каким
разрешением и почему остальные недоступны. Обновляется сканом и ручным
обновлением композиции; читается модалкой статуса.
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE video_availability (
    series_id INTEGER NOT NULL,
    report TEXT NOT NULL,
    checked_at DATETIME NOT NULL,
    PRIMARY KEY (series_id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""")


def downgrade() -> None:
    op.execute("DROP TABLE video_availability")
