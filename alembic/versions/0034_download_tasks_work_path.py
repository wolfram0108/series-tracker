"""downloads: where a task writes and what it wrote, and the name a ready file is on its way to.

download_tasks.work_path: a task writes into a hidden file of its own and puts it under the name of the video when it is
whole. Where it writes does not depend on where the video will lie, which the plan may change while the task waits or runs,
so it is a column of its own, fixed when the task is made. A task made before this wrote beside the name of the video, and
the upgrade writes that path into the column, so that no rule read at run time has to guess it.

download_tasks.work_size, work_ino: what the whole work file was when its publication began (size and file number). Once
the work file is gone, a file under the name of the video is proved to be this one only if it is this file; the mere fact
that something lies there proves nothing.

media_items.rename_to: the name a ready file is being moved to. It is written before the file moves and cleared after the
name is recorded, so that a stop in between leaves the obligation, not a file under one name and a row with another.

The downgrade does not lose work: a file that is whole and waits for its name, and a rename that is not finished, have no
place in the earlier schema, so it refuses until they are done.
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def legacy_work_path(save_path: str | None, key: str) -> str | None:
    """Where a task of the earlier schema wrote: a hidden file beside the name of the video."""
    if not save_path:
        return None
    directory, name = os.path.split(save_path)
    return os.path.join(directory, f".{key}.{name}")


def upgrade() -> None:
    op.execute("ALTER TABLE download_tasks ADD COLUMN work_path TEXT")
    op.execute("ALTER TABLE download_tasks ADD COLUMN work_size INTEGER")
    op.execute("ALTER TABLE download_tasks ADD COLUMN work_ino INTEGER")
    op.execute("ALTER TABLE media_items ADD COLUMN rename_to TEXT")
    bind = op.get_bind()
    for task in bind.execute(sa.text("SELECT id, task_key, save_path FROM download_tasks WHERE work_path IS NULL")).fetchall():
        path = legacy_work_path(task.save_path, task.task_key)
        if path is not None:
            bind.execute(sa.text("UPDATE download_tasks SET work_path=:path WHERE id=:id"), {"path": path, "id": task.id})


def downgrade() -> None:
    bind = op.get_bind()
    waiting = bind.execute(sa.text(
        "SELECT task_key, save_path, work_path FROM download_tasks WHERE status='publishing' AND work_path IS NOT NULL")).fetchall()
    stranded = [task.task_key for task in waiting if task.work_path != legacy_work_path(task.save_path, task.task_key)]
    moving = bind.execute(sa.text("SELECT unique_id FROM media_items WHERE rename_to IS NOT NULL")).fetchall()
    if stranded or moving:
        raise RuntimeError(
            "downgrade refused: whole files wait for their names in work files the earlier schema cannot name "
            f"({len(stranded)} tasks) and renames are not finished ({len(moving)} rows); let the service finish them first")
    op.execute("ALTER TABLE media_items DROP COLUMN rename_to")
    op.execute("ALTER TABLE download_tasks DROP COLUMN work_ino")
    op.execute("ALTER TABLE download_tasks DROP COLUMN work_size")
    op.execute("ALTER TABLE download_tasks DROP COLUMN work_path")
