"""The tab «Контейнеры» of the torrent editor becomes two (stage 15): «Озв. конт.» — the sound inside the video files — and
«Суб. конт.» — the subtitles inside them, each with its profile of rules. The profile of the old tab goes to the first (its
rules were written for the sound and the subtitles alike; the owner moves the rules of subtitles); the second gets its own
when the editor is next opened. A placement by hand in a container follows the kind of its stream."""
import json

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

TABS = "('files', 'voices', 'subtitles', 'voice_streams', 'subtitle_streams')"


def upgrade() -> None:
    conn = op.get_bind()
    op.execute("ALTER TABLE unpack_sources RENAME COLUMN streams_profile_id TO voice_streams_profile_id")
    op.execute("ALTER TABLE unpack_sources ADD COLUMN subtitle_streams_profile_id INTEGER REFERENCES parser_profiles (id)")
    op.execute("UPDATE parser_profiles SET name = replace(name, ' · контейнеры', ' · озв. конт.') WHERE kind = 'torrent'")
    # The kind of every stream of a container, by the key the editor names it with (release/path#index).
    kinds = {}
    for url, release, path, streams in conn.exec_driver_sql("SELECT url, release, path, streams FROM unpack_streams"):
        for stream in json.loads(streams or "[]"):
            kinds[(url, f"{release}/{path}#{stream.get('index')}")] = stream.get("kind")
    rows = conn.exec_driver_sql("SELECT url, tab, item, season, episode, episode_last, folder FROM unpack_assignments").fetchall()
    op.execute(f"""
        CREATE TABLE unpack_assignments_new (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN {TABS}),
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
    for url, tab, item, season, episode, episode_last, folder in rows:
        if tab == "streams":
            tab = "subtitle_streams" if kinds.get((url, item)) == "subtitle" else "voice_streams"
        conn.exec_driver_sql("INSERT INTO unpack_assignments_new VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (url, tab, item, season, episode, episode_last, folder))
    op.execute("DROP TABLE unpack_assignments")
    op.execute("ALTER TABLE unpack_assignments_new RENAME TO unpack_assignments")


def downgrade() -> None:
    op.execute("ALTER TABLE unpack_sources DROP COLUMN subtitle_streams_profile_id")
    op.execute("ALTER TABLE unpack_sources RENAME COLUMN voice_streams_profile_id TO streams_profile_id")
