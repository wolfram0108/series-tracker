"""voices: the identity of the video a voice-over was aligned to (ЭТАП_11, point 5).

`voice_dubs.aligned_to` is `<source id>:<row key>` of the reference the job was sent with. A track stays valid while the
reference has the same identity, whatever its resolution: a better version of the same video needs no new alignment.
A row aligned before this column is judged by `ref_uid`, as before.
"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_dubs ADD COLUMN aligned_to TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_dubs DROP COLUMN aligned_to")
