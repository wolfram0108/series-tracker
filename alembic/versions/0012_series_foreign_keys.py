"""series_foreign_keys — внешние ключи на series во всех серийных таблицах.

Половина таблиц с series_id не проверяла существование серии: опоздавшая
запись (скан удалённой серии) оседала в них навсегда. С FK такая запись
отбивается, а удаление серии по-прежнему идёт с enforce_fk=False —
родителя удаляет catalog, дети дочищают владельцы по событию (Р-19).
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# table: (ddl без внешнего ключа, ddl с внешним ключом, колонки, индексы)
_TABLES = {
    "download_tasks": (
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    task_key TEXT NOT NULL,
    series_id INTEGER NOT NULL,
    video_url TEXT,
    save_path TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    attempts INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    task_type TEXT NOT NULL,
    progress INTEGER,
    dlspeed INTEGER,
    eta INTEGER,
    total_size_mb FLOAT,
    PRIMARY KEY (id)
)""",
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    task_key TEXT NOT NULL,
    series_id INTEGER NOT NULL,
    video_url TEXT,
    save_path TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    attempts INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    task_type TEXT NOT NULL,
    progress INTEGER,
    dlspeed INTEGER,
    eta INTEGER,
    total_size_mb FLOAT,
    PRIMARY KEY (id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""",
        "id, task_key, series_id, video_url, save_path, status, "
        "error_message, attempts, created_at, updated_at, task_type, "
        "progress, dlspeed, eta, total_size_mb",
        (("ix_download_tasks_task_key",
          "CREATE INDEX ix_download_tasks_task_key ON {name} (task_key)"),),
    ),
    "scan_tasks": (
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    created_at DATETIME,
    status TEXT,
    task_data TEXT,
    results_data TEXT,
    PRIMARY KEY (id)
)""",
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    created_at DATETIME,
    status TEXT,
    task_data TEXT,
    results_data TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""",
        "id, series_id, created_at, status, task_data, results_data",
        (),
    ),
    "slicing_tasks": (
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    media_item_unique_id TEXT NOT NULL,
    series_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress_chapters TEXT,
    error_message TEXT,
    created_at DATETIME,
    PRIMARY KEY (id)
)""",
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    media_item_unique_id TEXT NOT NULL,
    series_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress_chapters TEXT,
    error_message TEXT,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""",
        "id, media_item_unique_id, series_id, status, progress_chapters, "
        "error_message, created_at",
        (("ix_slicing_tasks_media_item_unique_id",
          "CREATE INDEX ix_slicing_tasks_media_item_unique_id "
          "ON {name} (media_item_unique_id)"),),
    ),
    "renaming_tasks": (
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    media_item_unique_id TEXT,
    old_path TEXT,
    new_path TEXT,
    status TEXT NOT NULL,
    attempts INTEGER,
    error_message TEXT,
    created_at DATETIME,
    task_type TEXT NOT NULL,
    task_data TEXT,
    PRIMARY KEY (id)
)""",
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    media_item_unique_id TEXT,
    old_path TEXT,
    new_path TEXT,
    status TEXT NOT NULL,
    attempts INTEGER,
    error_message TEXT,
    created_at DATETIME,
    task_type TEXT NOT NULL,
    task_data TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""",
        "id, series_id, media_item_unique_id, old_path, new_path, status, "
        "attempts, error_message, created_at, task_type, task_data",
        (("ix_renaming_tasks_series_id",
          "CREATE INDEX ix_renaming_tasks_series_id ON {name} (series_id)"),
         ("ix_renaming_tasks_media_item_unique_id",
          "CREATE INDEX ix_renaming_tasks_media_item_unique_id "
          "ON {name} (media_item_unique_id)")),
    ),
    "relocation_tasks": (
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    new_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at DATETIME,
    PRIMARY KEY (id)
)""",
        """CREATE TABLE {name} (
    id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    new_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""",
        "id, series_id, new_path, status, error_message, created_at",
        (("ix_relocation_tasks_series_id",
          "CREATE INDEX ix_relocation_tasks_series_id ON {name} (series_id)"),),
    ),
}


def _rebuild(table: str, ddl: str, columns: str, indexes: tuple) -> None:
    """Пересоздать таблицу с заданным DDL, вернуть её индексы.

    foreign_keys выключается вне транзакции (внутри pragma не действует),
    legacy_alter_table — чтобы переименование не переписывало ссылки на
    удалённую таблицу в чужих схемах.
    """
    tmp = f"{table}_new"
    with op.get_context().autocommit_block():
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("PRAGMA legacy_alter_table=ON")
        op.execute("BEGIN")
        op.execute(ddl.format(name=tmp))
        op.execute(f"INSERT INTO {tmp} ({columns}) "
                   f"SELECT {columns} FROM {table}")
        op.execute(f"DROP TABLE {table}")
        op.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        op.execute("COMMIT")
        for _name, index_sql in indexes:
            op.execute(index_sql.format(name=table))
        op.execute("PRAGMA legacy_alter_table=OFF")


def upgrade() -> None:
    for table, (plain, with_fk, columns, indexes) in _TABLES.items():
        _rebuild(table, with_fk, columns, indexes)


def downgrade() -> None:
    for table, (plain, _with_fk, columns, indexes) in _TABLES.items():
        _rebuild(table, plain, columns, indexes)
