"""The tasks of the mass renaming leave (stage 15, the kind of a series is gone): the renaming of the names of a torrent
series before its scan had one caller, the torrent branch of the scan, which left with the kind; renaming keeps no table.
"""
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS renaming_tasks")


def downgrade() -> None:
    raise NotImplementedError("the tasks of the mass renaming are not made again: their code is gone (restore the backup)")
