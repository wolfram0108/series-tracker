"""voice_dubs gains the alignment outcome (mission voice-alignment, stage 7).

job_id: the daemon job that carries the voice-over; aligned_path: the aligned track before it is
placed; sidecar_path: the track next to the reference video; outcome: why a voice-over was rejected.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("job_id TEXT", "aligned_path TEXT", "sidecar_path TEXT", "outcome TEXT"):
        op.execute(f"ALTER TABLE voice_dubs ADD COLUMN {column}")
    op.execute("CREATE INDEX ix_voice_dubs_job ON voice_dubs (job_id)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_voice_dubs_job")
    for column in ("job_id", "aligned_path", "sidecar_path", "outcome"):
        op.execute(f"ALTER TABLE voice_dubs DROP COLUMN {column}")
