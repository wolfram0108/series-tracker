"""voice_cleanup keeps the folder a job writes in.

A job that could not be confirmed stopped is remembered with the folder it writes to, so that the folder is
cleaned once the job is stopped and nothing else is mistaken for its resources.
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_cleanup ADD COLUMN scope TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_cleanup DROP COLUMN scope")
