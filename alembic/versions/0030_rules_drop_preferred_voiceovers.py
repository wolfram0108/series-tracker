"""rules: parser_profiles.preferred_voiceovers goes.

Nothing reads or edits the column: the engine ignores it, the interface has no field for it, and on the live base
every value is empty. A voiceover is named by the constructor's action "assign voiceover" and grouped by `voices`.

downgrade brings the column back empty; the values it held cannot come back.
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_profiles DROP COLUMN preferred_voiceovers")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_profiles ADD COLUMN preferred_voiceovers TEXT")
