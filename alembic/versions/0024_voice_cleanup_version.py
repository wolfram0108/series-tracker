"""voice_cleanup entries carry a version.

An entry is ended only in the version that was read: when the same name is handed over again meanwhile, the
earlier pass can no longer end the new entry.
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE voice_cleanup ADD COLUMN version INTEGER NOT NULL DEFAULT 1")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_cleanup DROP COLUMN version")
