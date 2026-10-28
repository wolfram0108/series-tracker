"""voice_dubs remembers the reference an alignment was made against (mission voice-alignment, stage 8).

ref_uid: the reference's media item when the job was sent; ref_path: its file. A track is valid
only for that reference: a different item makes it stale, a moved or renamed file only moves it.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("ref_uid TEXT", "ref_path TEXT"):
        op.execute(f"ALTER TABLE voice_dubs ADD COLUMN {column}")


def downgrade() -> None:
    for column in ("ref_uid", "ref_path"):
        op.execute(f"ALTER TABLE voice_dubs DROP COLUMN {column}")
