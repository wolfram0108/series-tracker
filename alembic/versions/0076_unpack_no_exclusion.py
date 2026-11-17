"""The torrent editor places, it does not leave out (stage 15): an item is placed (green, a row) or not (red, no row); what
goes out of the output is the work of the constructor of the series after it. The hand's «Не брать» leaves with its column:
the items it held become unplaced. A folder of subtitles has a type of what it is (full, signs); how a track lies in the
container is the output's."""
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM unpack_assignments WHERE excluded = 1")
    op.execute("""
        CREATE TABLE unpack_assignments_new (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN ('files', 'voices', 'subtitles', 'streams')),
            item TEXT NOT NULL,
            season INTEGER,
            episode INTEGER,
            episode_last INTEGER,
            folder TEXT,
            PRIMARY KEY (url, tab, item),
            CHECK ((tab = 'files' AND season IS NOT NULL AND episode IS NOT NULL AND folder IS NULL
                    AND (episode_last IS NULL OR episode_last > episode))
                   OR (tab <> 'files' AND folder IS NOT NULL AND season IS NULL AND episode IS NULL
                       AND episode_last IS NULL))
        )
    """)
    op.execute("INSERT INTO unpack_assignments_new (url, tab, item, season, episode, episode_last, folder) "
               "SELECT url, tab, item, season, episode, episode_last, folder FROM unpack_assignments")
    op.execute("DROP TABLE unpack_assignments")
    op.execute("ALTER TABLE unpack_assignments_new RENAME TO unpack_assignments")
    op.execute("UPDATE unpack_folders SET subtitle_type = '' WHERE subtitle_type NOT IN ('', 'full', 'signs')")
    op.execute("""
        CREATE TABLE unpack_folders_new (
            url TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('voice', 'subtitles')),
            name TEXT NOT NULL CHECK (name <> ''),
            language TEXT NOT NULL DEFAULT '',
            subtitle_type TEXT NOT NULL DEFAULT '' CHECK (subtitle_type IN ('', 'full', 'signs')),
            PRIMARY KEY (url, kind, name)
        )
    """)
    op.execute("INSERT INTO unpack_folders_new SELECT url, kind, name, language, subtitle_type FROM unpack_folders")
    op.execute("DROP TABLE unpack_folders")
    op.execute("ALTER TABLE unpack_folders_new RENAME TO unpack_folders")


def downgrade() -> None:
    op.execute("ALTER TABLE unpack_assignments ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
