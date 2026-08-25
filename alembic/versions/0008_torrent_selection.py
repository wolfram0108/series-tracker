"""torrent_selection — выборочная загрузка файлов торрент-раздачи.

Память выбора ведётся по номерам серий самого сериала, а не по строкам
файлов конкретной раздачи: релиз заменяется, файлы пересоздаются, а
«эту серию не качаем» обязано пережить замену. Отсюда две колонки в
series (рядом с ignored_seasons) и их проекция на файлы раздачи.

series.ignored_episodes — JSON {"<сезон>": [номера серий]}, владелец
catalog. torrent_files.is_excluded/exclude_reason — проекция для SQL-
агрегатов и приоритетов qBittorrent, владелец torrents. qb_file_index —
стабильный ключ файла в раздаче: имя мутирует при переименовании, индекс
в торренте не меняется никогда.
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series ADD COLUMN ignored_episodes TEXT")
    op.execute("UPDATE series SET ignored_episodes='{}' "
               "WHERE ignored_episodes IS NULL")
    op.execute("ALTER TABLE torrent_files ADD COLUMN is_excluded "
               "BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE torrent_files ADD COLUMN exclude_reason TEXT")
    op.execute("ALTER TABLE torrent_files ADD COLUMN qb_file_index INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE torrent_files DROP COLUMN qb_file_index")
    op.execute("ALTER TABLE torrent_files DROP COLUMN exclude_reason")
    op.execute("ALTER TABLE torrent_files DROP COLUMN is_excluded")
    op.execute("ALTER TABLE series DROP COLUMN ignored_episodes")
