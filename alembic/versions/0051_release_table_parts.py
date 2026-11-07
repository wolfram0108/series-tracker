"""sources: the parts of a page, so that one part that cannot be read does not cost the whole page.

`release_rows.part` is the part of the page a row was read from (the catalog of a player, the page of an episode); empty
for the rows the page lists itself. `release_tables.unread` is the JSON list of the parts the last read could not get
(`UnreadPart`). A table stored before this revision has `unread` NULL: its rows carry no part, so a part that fails is not
told apart in it, and such a page is refused as before until one read gets it whole.
"""
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE release_rows ADD COLUMN part TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE release_tables ADD COLUMN unread TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE release_tables DROP COLUMN unread")
    op.execute("ALTER TABLE release_rows DROP COLUMN part")
