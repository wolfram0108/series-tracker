"""sources, composition: the rows of the tables of pages (release_tables.rows) and of the layouts of series
(composition_state.rows, .subtitles) are kept by columns (core.native) instead of the JSON of their models: the count reads
them in place, nothing is parsed and no object is made of each row.
"""
from alembic import op

from core import native

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

_COLS = b"COLS"


def _bytes(value) -> bytes:
    return value.encode() if isinstance(value, str) else bytes(value)


def _convert(conn, select: str, update: str, change) -> None:
    for key, *fields in conn.exec_driver_sql(select).fetchall():
        made = [None if field is None else change(_bytes(field)) for field in fields]
        conn.exec_driver_sql(update, (*made, key))


def upgrade() -> None:
    conn = op.get_bind()
    _convert(conn, "SELECT url, rows FROM release_tables WHERE rows IS NOT NULL",
             "UPDATE release_tables SET rows = ? WHERE url = ?",
             lambda field: field if field.startswith(_COLS) else native.encode_table(field))
    _convert(conn, "SELECT series_id, rows, subtitles FROM composition_state WHERE rows IS NOT NULL OR subtitles IS NOT NULL",
             "UPDATE composition_state SET rows = ?, subtitles = ? WHERE series_id = ?",
             lambda field: field if field.startswith(_COLS) else native.Rows.from_json(field).blob)


def downgrade() -> None:
    conn = op.get_bind()
    _convert(conn, "SELECT url, rows FROM release_tables WHERE rows IS NOT NULL",
             "UPDATE release_tables SET rows = ? WHERE url = ?",
             lambda field: native.table_json(field) if field.startswith(_COLS) else field)
    _convert(conn, "SELECT series_id, rows, subtitles FROM composition_state WHERE rows IS NOT NULL OR subtitles IS NOT NULL",
             "UPDATE composition_state SET rows = ?, subtitles = ? WHERE series_id = ?",
             lambda field: native.Rows(field).json(True) if field.startswith(_COLS) else field)
