"""voice_dubs keeps the folder its job asked the service to write to.

The tracks of a job are looked for where the job was asked to put them, whatever the settings or the
folders of the single voice-overs are by the time the job ends.
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_dubs ADD COLUMN out_dir TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_dubs DROP COLUMN out_dir")
