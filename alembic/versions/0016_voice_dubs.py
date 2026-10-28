"""voice_dubs — additional voice-overs of an episode (mission voice-alignment).

One row per (series, episode, voice): which sources can give it, where the temporary
file lies and how far the download went. Owned by the voices module.
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE voice_dubs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " season INTEGER NOT NULL,"
        " episode INTEGER NOT NULL,"
        " site_episode INTEGER NOT NULL,"
        " voice_key TEXT NOT NULL,"
        " voice_label TEXT NOT NULL,"
        " chain TEXT NOT NULL,"
        " attempt INTEGER NOT NULL DEFAULT 0,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " temp_path TEXT,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " UNIQUE (series_id, season, episode, voice_key))")
    op.execute("CREATE INDEX ix_voice_dubs_series ON voice_dubs (series_id)")


def downgrade() -> None:
    op.execute("DROP TABLE voice_dubs")
