"""catalog: a source loses the name of its voice-over (`series_sources.voice_name`, 0033).

A voice-over is named by the rules and the groups; a name given to the whole source said nothing where a source has
several voice-overs, and no source of the owner held one. On the way back every source has none (''), as 0033 made it.
"""
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources DROP COLUMN voice_name")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN voice_name TEXT NOT NULL DEFAULT ''")
