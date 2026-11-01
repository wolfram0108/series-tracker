"""sources: tables of site pages.

`release_tables` is one row per page: the head of the last complete table plus the state of the read in progress.
`release_rows` holds the rows of that complete table; a page's rows are replaced in one transaction.
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE release_tables ("
        " url TEXT PRIMARY KEY,"
        " site TEXT NOT NULL,"
        " names TEXT NOT NULL DEFAULT '[]',"
        " site_voice TEXT NOT NULL DEFAULT '',"
        " read_at TEXT,"
        " complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),"
        " state TEXT NOT NULL CHECK (state IN ('reading', 'ready', 'failed')),"
        " reason TEXT,"
        " reading_since TEXT)")
    op.execute(
        "CREATE TABLE release_rows ("
        " url TEXT NOT NULL REFERENCES release_tables (url) ON DELETE CASCADE,"
        " seq INTEGER NOT NULL,"
        ' "key" TEXT NOT NULL,'
        " player TEXT NOT NULL,"
        " player_label TEXT NOT NULL DEFAULT '',"
        " grp TEXT NOT NULL DEFAULT '',"
        " text TEXT NOT NULL DEFAULT '',"
        " voice_name TEXT NOT NULL DEFAULT '',"
        " voice_kind TEXT NOT NULL DEFAULT '',"
        " heights TEXT NOT NULL DEFAULT '[]',"
        " state TEXT NOT NULL CHECK (state IN ('playable', 'absent', 'not_obtained')),"
        " reason TEXT,"
        " duration_s REAL,"
        " published TEXT,"
        " link TEXT NOT NULL DEFAULT '',"
        " site_fields TEXT NOT NULL DEFAULT '{}',"
        ' PRIMARY KEY (url, "key"),'
        " UNIQUE (url, seq))")


def downgrade() -> None:
    op.execute("DROP TABLE release_rows")
    op.execute("DROP TABLE release_tables")
