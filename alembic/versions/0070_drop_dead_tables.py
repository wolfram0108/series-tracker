"""The tables no module reads or writes since the old path of the video series left (stage 12, 44168f2f, 2026-11-08): the
records of the voice-overs, cuts, files, cleanups and epochs of the old voices module, the download tasks and publications
of the old downloads module. The asset of every series lives in plan_assets since then; nothing reads these. And the found
folders of the shares of 139 (migration 0067), taken over by the provider of the socket (its import copied both rows,
2026-11-14): the tracker neither reads nor writes them since.
"""
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

DEAD = ("voice_cuts", "voice_dubs", "voice_epoch", "voice_files", "voice_cleanup", "download_tasks", "pack_publications",
        "share139_folders")


def upgrade() -> None:
    for table in DEAD:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    raise NotImplementedError("the tables of the old path are not made again: their code is gone (restore the backup)")
