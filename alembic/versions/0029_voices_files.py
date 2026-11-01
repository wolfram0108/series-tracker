"""voices: voice-overs are planned from the layout, which has no site episode; a sync of a series is remembered.

Step one of the package: `voice_dubs.site_episode` goes (the episode of a voice-over is the layout's, in `season` and
`episode`; the other columns and every row stay as they are), and `voice_series` keeps the series a sync was asked for,
the only ones whose waiting voice-overs are downloaded. A series that already has voice-overs is entered in it, so
that what was waiting before the migration goes on. Step two adds `voice_files` (the file of a source row shared by
the episodes of a compilation).

downgrade brings `site_episode` back as 0: the numbers it held cannot come back.
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_dubs DROP COLUMN site_episode")
    op.execute(
        "CREATE TABLE voice_series ("
        " series_id INTEGER PRIMARY KEY REFERENCES series (id),"
        " allowed_at DATETIME NOT NULL)")
    op.execute(
        "INSERT INTO voice_series (series_id, allowed_at) "
        "SELECT DISTINCT series_id, strftime('%Y-%m-%d %H:%M:%f', 'now') FROM voice_dubs")


def downgrade() -> None:
    op.execute("DROP TABLE voice_series")
    op.execute("ALTER TABLE voice_dubs ADD COLUMN site_episode INTEGER NOT NULL DEFAULT 0")
