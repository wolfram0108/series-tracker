"""The torrent source (stage 15): the releases of a page and their files (module `torrents`), the probed streams, the manual
layout and the rules of the torrent editor (module `unpack`), the kind of a profile of rules.

A release is keyed by the page and its own id on the page, not by a series: the page of one tracker in two series is one
download. What rules give is counted from the rules every time (a rule reaches new files of an update); only what the user
placed by hand is stored, in the terms of the domain: a season and an episode, the name of a folder.
"""
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE torrent_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            release TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            published TEXT,
            infohash TEXT,
            state TEXT NOT NULL CHECK (state IN ('listed', 'metadata', 'downloading', 'ready', 'updating', 'failed',
                                                 'archived')),
            reason TEXT,
            watched INTEGER NOT NULL DEFAULT 1 CHECK (watched IN (0, 1)),
            folder TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (url, release)
        )
    """)
    op.execute("CREATE UNIQUE INDEX ix_torrent_releases_infohash ON torrent_releases (infohash) WHERE infohash IS NOT NULL")
    op.execute("""
        CREATE TABLE torrent_release_files (
            release_id INTEGER NOT NULL REFERENCES torrent_releases (id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            size INTEGER NOT NULL CHECK (size >= 0),
            progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
            priority INTEGER NOT NULL DEFAULT 1 CHECK (priority >= 0),
            PRIMARY KEY (release_id, path)
        )
    """)
    op.execute("""
        CREATE TABLE torrent_release_versions (
            release_id INTEGER NOT NULL REFERENCES torrent_releases (id) ON DELETE CASCADE,
            infohash TEXT NOT NULL,
            published TEXT,
            added_at TEXT NOT NULL,
            PRIMARY KEY (release_id, infohash)
        )
    """)
    # A file is probed again only when its version or its size is another than the probe read (the video track of a
    # source is never compared by content: stage 15, the identity of a file is its path).
    op.execute("""
        CREATE TABLE unpack_streams (
            url TEXT NOT NULL,
            release TEXT NOT NULL,
            path TEXT NOT NULL,
            infohash TEXT NOT NULL,
            size INTEGER NOT NULL,
            streams TEXT NOT NULL DEFAULT '[]',
            attachments TEXT NOT NULL DEFAULT '[]',
            chapters INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            PRIMARY KEY (url, release, path)
        )
    """)
    op.execute("""
        CREATE TABLE unpack_folders (
            url TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('voice', 'subtitles')),
            name TEXT NOT NULL CHECK (name <> ''),
            language TEXT NOT NULL DEFAULT '',
            subtitle_type TEXT NOT NULL DEFAULT '' CHECK (subtitle_type IN ('', 'full', 'signs', 'forced')),
            PRIMARY KEY (url, kind, name)
        )
    """)
    # `item` is a path of the release, or `path#index` for a stream of a container; a file of the tab «Файлы» is placed
    # in a season and an episode (to `episode_last` for a file of several), an item of the other tabs in a folder by its
    # name; `excluded` is «Не брать». An empty season or episode the editor makes holds nothing and is not stored.
    op.execute("""
        CREATE TABLE unpack_assignments (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN ('files', 'voices', 'subtitles', 'streams')),
            item TEXT NOT NULL,
            season INTEGER,
            episode INTEGER,
            episode_last INTEGER,
            folder TEXT,
            excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
            PRIMARY KEY (url, tab, item),
            CHECK (excluded = 1
                   OR (tab = 'files' AND season IS NOT NULL AND episode IS NOT NULL AND folder IS NULL
                       AND (episode_last IS NULL OR episode_last > episode))
                   OR (tab <> 'files' AND folder IS NOT NULL AND season IS NULL AND episode IS NULL
                       AND episode_last IS NULL))
        )
    """)
    op.execute("""
        CREATE TABLE unpack_sources (
            url TEXT PRIMARY KEY,
            voices_profile_id INTEGER REFERENCES parser_profiles (id),
            subtitles_profile_id INTEGER REFERENCES parser_profiles (id),
            streams_profile_id INTEGER REFERENCES parser_profiles (id)
        )
    """)
    # The profiles of the torrent editor are not profiles of a series: the list of the rules of the series shows none of them.
    op.execute("ALTER TABLE parser_profiles ADD COLUMN kind TEXT NOT NULL DEFAULT 'series' "
               "CHECK (kind IN ('series', 'torrent'))")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_profiles DROP COLUMN kind")
    for table in ("unpack_sources", "unpack_assignments", "unpack_folders", "unpack_streams",
                  "torrent_release_versions", "torrent_release_files", "torrent_releases"):
        op.execute(f"DROP TABLE {table}")
