"""A track the user took out of a folder of the torrent editor («−») is in no folder — red — whatever the titles in the files
and the rules say, until it is placed by hand or «Авто» gives the automatic layout back (stage 15)."""
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE unpack_removed (
            url TEXT NOT NULL,
            tab TEXT NOT NULL CHECK (tab IN ('voices', 'subtitles', 'voice_streams', 'subtitle_streams')),
            item TEXT NOT NULL,
            PRIMARY KEY (url, tab, item)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE unpack_removed")
