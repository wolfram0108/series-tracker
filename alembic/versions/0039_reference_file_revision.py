"""The revision of the file of a row, and the revision of the reference a voice-over was judged against.

`media_items.file_rev` (owned by downloads) counts the files a row has taken: a download published under its name, or a
file found there. A container built of the same video is not a new file. `voice_dubs.ref_rev` (owned by voices) is the
revision of the reference file a voice-over was sent with: a reference downloaded again is a new file, and every verdict on
the old one (an alignment, a refusal) is given again. Rows judged before this column count as judged against the file the
row holds now (0 = 0).
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_items ADD COLUMN file_rev INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE voice_dubs ADD COLUMN ref_rev INTEGER")
    op.execute("UPDATE voice_dubs SET ref_rev = 0 WHERE ref_uid IS NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE voice_dubs DROP COLUMN ref_rev")
    op.execute("ALTER TABLE media_items DROP COLUMN file_rev")
