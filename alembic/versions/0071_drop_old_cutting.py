"""The old path of the cutting of compilations leaves (the owner's word, 2026-11-17): its tables media_items (rows of the
video series before the plan, written by nothing since 2026-11-08), slicing_tasks and sliced_files; and scan_references,
the reference of every slot chosen by the old scan, deleted by nothing but the deletion of a series since the plan chooses
it. The setting of the old slicing (`slicing_delete_source_file`) goes with them.
"""
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

DEAD = ("sliced_files", "slicing_tasks", "media_items", "scan_references")


def upgrade() -> None:
    for table in DEAD:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DELETE FROM settings WHERE key = 'slicing_delete_source_file'")


def downgrade() -> None:
    raise NotImplementedError("the tables of the old cutting are not made again: their code is gone (restore the backup)")
