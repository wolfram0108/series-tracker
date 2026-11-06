"""downloads: a task may replace the file its row holds (`download_tasks.replace_file`): the user asked for the video of the
row to be downloaded again. Such a task is wanted while its row is ready, and the row keeps its file until the new one
takes its name.
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE download_tasks ADD COLUMN replace_file INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE download_tasks DROP COLUMN replace_file")
