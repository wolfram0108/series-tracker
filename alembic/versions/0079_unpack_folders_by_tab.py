"""A folder of the torrent editor belongs to its tab (stage 15): «Озвучки» and «Озв. конт.», «Субтитры» and «Суб. конт.» are
different sets and do not mix. A folder kept by its kind (voice, subtitles) goes to both tabs of the kind with its settings,
so that nothing the user set is lost; a folder of the automatic layout he deleted stays deleted in both."""
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

TABS = "('voices', 'subtitles', 'voice_streams', 'subtitle_streams')"
BY_KIND = (("voice", "voices"), ("voice", "voice_streams"), ("subtitles", "subtitles"), ("subtitles", "subtitle_streams"))


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE unpack_folders_new (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN {TABS}),
            name TEXT NOT NULL CHECK (name <> ''),
            language TEXT NOT NULL DEFAULT '',
            subtitle_type TEXT NOT NULL DEFAULT '' CHECK (subtitle_type IN ('', 'full', 'signs')),
            PRIMARY KEY (url, tab, name)
        )
    """)
    op.execute(f"""
        CREATE TABLE unpack_hidden_new (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN {TABS}),
            name TEXT NOT NULL CHECK (name <> ''),
            PRIMARY KEY (url, tab, name)
        )
    """)
    for kind, tab in BY_KIND:
        op.execute(f"INSERT INTO unpack_folders_new SELECT url, '{tab}', name, language, subtitle_type FROM unpack_folders "
                   f"WHERE kind = '{kind}'")
        op.execute(f"INSERT INTO unpack_hidden_new SELECT url, '{tab}', name FROM unpack_hidden WHERE kind = '{kind}'")
    for table in ("unpack_folders", "unpack_hidden"):
        op.execute(f"DROP TABLE {table}")
        op.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def downgrade() -> None:
    raise NotImplementedError("the folders of two tabs of a kind cannot be told back as one")
