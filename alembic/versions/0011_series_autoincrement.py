"""series_autoincrement — номера серий не переиспользуются.

Удалённый максимальный id занимался следующей вставкой: новая серия
получала номер удалённой, а вместе с ним те же media_unique_id (в хэше
участвует series_id) — записи новой серии приклеивались к старым.
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_COLUMNS = (
    "id, url, name, name_en, site, save_path, season, quality, state, "
    "last_scan_time, auto_scan_enabled, quality_override, "
    "resolution_override, source_type, parser_profile_id, ignored_seasons, "
    "vk_search_mode, vk_quality_priority, ignored_episodes"
)

_NEW_SERIES = """
CREATE TABLE series_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL,
    site TEXT NOT NULL,
    save_path TEXT NOT NULL,
    season TEXT,
    quality TEXT,
    state TEXT,
    last_scan_time DATETIME,
    auto_scan_enabled BOOLEAN NOT NULL,
    quality_override TEXT,
    resolution_override TEXT,
    source_type TEXT NOT NULL,
    parser_profile_id INTEGER,
    ignored_seasons TEXT,
    vk_search_mode TEXT NOT NULL,
    vk_quality_priority TEXT,
    ignored_episodes TEXT,
    FOREIGN KEY(parser_profile_id) REFERENCES parser_profiles (id)
)
"""

_OLD_SERIES = """
CREATE TABLE series_new (
    id INTEGER NOT NULL,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL,
    site TEXT NOT NULL,
    save_path TEXT NOT NULL,
    season TEXT,
    quality TEXT,
    state TEXT,
    last_scan_time DATETIME,
    auto_scan_enabled BOOLEAN NOT NULL,
    quality_override TEXT,
    resolution_override TEXT,
    source_type TEXT NOT NULL,
    parser_profile_id INTEGER,
    ignored_seasons TEXT,
    vk_search_mode TEXT NOT NULL,
    vk_quality_priority TEXT,
    ignored_episodes TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(parser_profile_id) REFERENCES parser_profiles (id)
)
"""


def _rebuild(ddl: str) -> None:
    """Пересоздать таблицу series с заданным DDL.

    rebuild идёт в явной транзакции, а foreign_keys выключается вне её:
    pragma внутри транзакции не действует. legacy_alter_table нужен,
    чтобы переименование не переписывало ссылки на series в чужих
    таблицах (они ссылаются на таблицу, которую мы уже удалили).
    """
    with op.get_context().autocommit_block():
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("PRAGMA legacy_alter_table=ON")
        op.execute("BEGIN")
        op.execute(ddl)
        op.execute(f"INSERT INTO series_new ({_COLUMNS}) "
                   f"SELECT {_COLUMNS} FROM series")
        op.execute("DROP TABLE series")
        op.execute("ALTER TABLE series_new RENAME TO series")
        op.execute("COMMIT")
        op.execute("PRAGMA legacy_alter_table=OFF")


def upgrade() -> None:
    _rebuild(_NEW_SERIES)


def downgrade() -> None:
    _rebuild(_OLD_SERIES)
