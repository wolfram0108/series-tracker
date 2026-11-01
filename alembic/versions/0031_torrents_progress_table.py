"""torrents: the progress of the torrents for the interface gets a table of its own.

The rows of torrents lived in `download_tasks`, a table owned by `downloads`, told apart by `task_type`: two modules
wrote one table. `torrent_progress` is owned by `torrents` alone, one row per infohash (the key the monitor already
used), and `download_tasks` keeps the tasks of downloads only.

upgrade moves the rows of `download_tasks` with task_type 'torrent' (the latest one if a hash has several) and
deletes them there; the rows of video tasks stay. downgrade moves the rows back as they were written by the old
code (no video address, no path, no attempts) and drops the table.
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE torrent_progress (
    id INTEGER NOT NULL,
    qb_hash TEXT NOT NULL,
    series_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    dlspeed INTEGER NOT NULL DEFAULT 0,
    eta INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at DATETIME,
    updated_at DATETIME,
    PRIMARY KEY (id),
    UNIQUE (qb_hash),
    FOREIGN KEY(series_id) REFERENCES series (id)
)""")
    op.execute("CREATE INDEX ix_torrent_progress_series ON torrent_progress (series_id)")
    op.execute(
        "INSERT INTO torrent_progress (qb_hash, series_id, status, progress, dlspeed, eta, error_message, "
        "created_at, updated_at) "
        "SELECT task_key, series_id, status, COALESCE(progress, 0), COALESCE(dlspeed, 0), COALESCE(eta, 0), "
        "error_message, created_at, updated_at FROM download_tasks "
        "WHERE id IN (SELECT MAX(id) FROM download_tasks WHERE task_type = 'torrent' GROUP BY task_key) "
        "ORDER BY id")
    op.execute("DELETE FROM download_tasks WHERE task_type = 'torrent'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO download_tasks (task_key, series_id, status, error_message, attempts, created_at, updated_at, "
        "task_type, progress, dlspeed, eta) "
        "SELECT qb_hash, series_id, status, error_message, 0, created_at, updated_at, 'torrent', progress, "
        "dlspeed, eta FROM torrent_progress ORDER BY id")
    op.execute("DROP INDEX ix_torrent_progress_series")
    op.execute("DROP TABLE torrent_progress")
