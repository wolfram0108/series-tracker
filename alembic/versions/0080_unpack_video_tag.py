"""The tag of a video placed by hand in «Файлы» (stage 15): a version of an episode (DIR) is an episode of its own, `S01E21`
and `S01E21 DIR` two episodes and two variants of the output. A track of the other tabs has no tag: it goes with the video of
its episode. The placements kept have none."""
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE unpack_assignments_new (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN ('files', 'voices', 'subtitles', 'voice_streams', 'subtitle_streams')),
            item TEXT NOT NULL,
            season INTEGER,
            episode INTEGER,
            episode_last INTEGER,
            folder TEXT,
            tag TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (url, tab, item),
            CHECK ((tab = 'files' AND season IS NOT NULL AND episode IS NOT NULL AND folder IS NULL
                    AND (episode_last IS NULL OR episode_last > episode))
                   OR (tab <> 'files' AND folder IS NOT NULL AND season IS NULL AND episode IS NULL
                       AND episode_last IS NULL AND tag = ''))
        )
    """)
    op.execute("INSERT INTO unpack_assignments_new (url, tab, item, season, episode, episode_last, folder) "
               "SELECT url, tab, item, season, episode, episode_last, folder FROM unpack_assignments")
    op.execute("DROP TABLE unpack_assignments")
    op.execute("ALTER TABLE unpack_assignments_new RENAME TO unpack_assignments")


def downgrade() -> None:
    raise NotImplementedError("a tag of a version cannot be told back without the episode it makes")
