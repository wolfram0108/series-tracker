"""season_episode_counts — кэш числа серий по сезонам шоу (TMDB) для агрегата.

Многосезонные серии (режим «несколько сезонов», поле сезона пустое) не имеют
фиксированного числа серий на момент добавления: реальный набор сезонов
становится известен лишь из нейминга (торрент — Season NN после
переименования; VK — media_items.season после скана/композиции). Чтобы
агрегировать число серий по этим сезонам, НЕ дёргая TMDB на каждом
пересчёте, счётчики серий по сезонам шоу кэшируются здесь как JSON
{"<season_number>": episode_count}. total_episodes становится агрегатом
(суммой по реальным сезонам, сезон 0/спешелы исключаются). Сеть к TMDB —
только когда появился сезон, которого в кэше нет. Владелец — модуль metadata.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_tmdb_mappings "
               "ADD COLUMN season_episode_counts TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE series_tmdb_mappings "
               "DROP COLUMN season_episode_counts")
