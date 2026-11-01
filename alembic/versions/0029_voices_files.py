"""voices: voice-overs are planned from the layout; the video of a source row that covers several episodes is a file.

`voice_dubs.site_episode` goes (the episode of a voice-over is the layout's, in `season` and `episode`). `admitted` says
that a sync of the series released the row to download; the rows that exist are admitted, they were downloading under
the old rule. `file_id` links a voice-over to a row of `voice_files`: the video of a source row that covers several
episodes (a compilation), downloaded once and read by the voice-overs of all of them. A file is one row per
(series, source, row key, address); its folder is journaled when no voice-over reads it any more.

The other columns and every row stay as they are. downgrade drops the file rows and journals the files they held; a
voice-over that was downloading or waiting on such a file goes back to waiting. `site_episode` comes back as 0: the
numbers it held cannot come back.
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_dubs DROP COLUMN site_episode")
    op.execute(
        "CREATE TABLE voice_files ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " source_id INTEGER NOT NULL,"
        " row_key TEXT NOT NULL,"
        " url TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " attempt INTEGER NOT NULL DEFAULT 0,"
        " temp_path TEXT,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " UNIQUE (series_id, source_id, row_key, url))")
    op.execute("CREATE INDEX ix_voice_files_series ON voice_files (series_id)")
    op.execute("ALTER TABLE voice_dubs ADD COLUMN admitted INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE voice_dubs ADD COLUMN file_id INTEGER")
    op.execute("CREATE INDEX ix_voice_dubs_file ON voice_dubs (file_id)")


def downgrade() -> None:
    op.execute(
        "INSERT OR IGNORE INTO voice_cleanup (path, kind, error, updated_at) "
        "SELECT temp_path, 'path', 'handed over', strftime('%Y-%m-%d %H:%M:%f', 'now') "
        "FROM voice_files WHERE temp_path IS NOT NULL")
    op.execute("UPDATE voice_dubs SET status='pending', attempt=0 "
               "WHERE file_id IS NOT NULL AND status IN ('downloading', 'ready')")
    op.execute("DROP INDEX ix_voice_dubs_file")
    op.execute("ALTER TABLE voice_dubs DROP COLUMN file_id")
    op.execute("ALTER TABLE voice_dubs DROP COLUMN admitted")
    op.execute("DROP TABLE voice_files")
    op.execute("ALTER TABLE voice_dubs ADD COLUMN site_episode INTEGER NOT NULL DEFAULT 0")
