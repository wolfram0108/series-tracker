"""What a row carries (stage 15, step 8, DESIGN.md 7.1.1): `sound` — a video with its sound or a sound track, as every row
of a site is — or `subtitles`, a subtitles track of a torrent release. The kind of the voice-over a player names
(`voice_kind`) is no such mark: a «subtitles» translation of Kodik is a video with its sound. With it the language of
the track and the type of subtitles (full, signs, forced), as the torrent editor gives them: the container is written with
them.
"""
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


COLUMNS = (("track", "'sound'"), ("language", "''"), ("subtitle_type", "''"))


def upgrade() -> None:
    for table in ("release_rows", "composition_rows"):
        for column, default in COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")


def downgrade() -> None:
    for table in ("composition_rows", "release_rows"):
        for column, _ in reversed(COLUMNS):
            op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
