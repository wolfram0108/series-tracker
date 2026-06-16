"""year — год выхода сериала в маппинге TMDB (Р-24, имя каталога).

Год нужен для готового имени каталога «Имя (год) [tmdbid-XXXX]». Поиск
TMDB его уже отдаёт (first_air_date[:4]), но раньше он нигде не
сохранялся — в инфо-режиме (правка существующего сериала) имя каталога
выходило без года. Колонка хранит год как строку ("2016"), согласованно
с тем, как год представлен в контракте search. Владелец — модуль
metadata. Старые записи получают год при следующей TMDB-синхронизации.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_tmdb_mappings ADD COLUMN year VARCHAR(8)")


def downgrade() -> None:
    op.execute("ALTER TABLE series_tmdb_mappings DROP COLUMN year")
