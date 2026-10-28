"""voice_dubs names the finished artifact of a voice-over by what it is, not by its form.

The artifact is a file next to the video today (a sidecar); the column no longer says so.
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_dubs RENAME COLUMN sidecar_path TO output_path")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_dubs RENAME COLUMN output_path TO sidecar_path")
