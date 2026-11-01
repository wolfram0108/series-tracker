"""series_sources: the name of the voice-over of a source whose rows have none.

A VK channel gives no voice-over: the constructor assigns one, or the user names it once for the source. The name is
only a fallback: a name the constructor assigned, then the one the player or the site gave, come first. Every source
that exists has none ('').
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN voice_name TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources DROP COLUMN voice_name")
