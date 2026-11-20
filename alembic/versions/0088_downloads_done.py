"""downloads: the files fetches ended well with (download_done) — the path, the link and the cap it was fetched by, and the
size and the time of change of the file then. The same fetch asked again (a sync taken up after a stop) takes the file
that still lies there as it was, with no ask of its source.
"""
from alembic import op

revision = "0088"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE download_done (path TEXT PRIMARY KEY, url TEXT NOT NULL, max_height INTEGER,"
               " size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, done_at TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE download_done")
