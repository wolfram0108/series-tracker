"""voices: a voice-over that no source has for the episode is `absent`, not `failed`: an episode put together in part is
the normal case, not a failure. The rows recorded as failed for want of any source take the new step; their error goes,
as there was none.
"""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_WANT_OF_SOURCE = '{"code":"not_in_any_source"}'


def upgrade() -> None:
    op.execute(f"UPDATE voice_dubs SET status='absent', error=NULL WHERE status='failed' AND error='{_WANT_OF_SOURCE}'")


def downgrade() -> None:
    op.execute(f"UPDATE voice_dubs SET status='failed', error='{_WANT_OF_SOURCE}' WHERE status='absent'")
