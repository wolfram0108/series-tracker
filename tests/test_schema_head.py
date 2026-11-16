"""The service starts only on the schema of its code (core/schema.py): a database behind the last migration, or one the
migrations never touched, is refused with what to do; a database at the head passes."""
import sqlite3

import pytest

from core.schema import SchemaBehind, head_revision, require_head
from schema_db import migrated


def test_a_database_at_the_head_of_the_migrations_passes(tmp_path):
    require_head(migrated(tmp_path / "head.db"))


def test_a_database_behind_the_code_is_refused_with_both_revisions_and_what_to_run(tmp_path):
    path = migrated(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '0067'")
    with pytest.raises(SchemaBehind) as refused:
        require_head(path)
    assert "0067" in str(refused.value) and head_revision() in str(refused.value) and "alembic upgrade head" in str(refused.value)


def test_a_database_no_migration_touched_is_refused(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    with pytest.raises(SchemaBehind, match="без миграций"):
        require_head(path)
