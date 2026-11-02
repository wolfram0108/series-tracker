"""download_tasks: the path of the work file of a task.

A task writes into a hidden file of its own and puts it under the name of the video when it is whole. Where it writes does
not depend on where the video will lie, which the plan may change while the task waits or runs, so it is a column of its
own, fixed when the task is made. A task made before this has none: its work file is where the name of the video says.
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE download_tasks ADD COLUMN work_path TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE download_tasks DROP COLUMN work_path")
