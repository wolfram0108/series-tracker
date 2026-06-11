"""Окружение Alembic. Миграции пишутся вручную (op.execute), автогенерации
по метаданным нет — схема ведётся явно (И-4: формат данных — констрейнт).
URL БД: переменная окружения ST_DB_URL приоритетнее alembic.ini.
"""
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
db_url = os.environ.get("ST_DB_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section),
                                prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
