"""voice_dubs keeps the intent of what it is doing to files and to the alignment service.

move_from: the source of a track move that has begun (the destination is sidecar_path), so that an
interrupted move is finished, not forgotten. submit_key: the key of a job sent to the service, which
finds the job again when the answer to the request is lost. voice_cleanup: files and jobs that could
not be removed yet and are retried.
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("move_from TEXT", "submit_key TEXT"):
        op.execute(f"ALTER TABLE voice_dubs ADD COLUMN {column}")
    op.execute("CREATE INDEX ix_voice_dubs_submit_key ON voice_dubs (submit_key)")
    op.execute(
        "CREATE TABLE voice_cleanup ("
        " path TEXT PRIMARY KEY,"
        " kind TEXT NOT NULL,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE voice_cleanup")
    op.execute("DROP INDEX ix_voice_dubs_submit_key")
    for column in ("move_from", "submit_key"):
        op.execute(f"ALTER TABLE voice_dubs DROP COLUMN {column}")
