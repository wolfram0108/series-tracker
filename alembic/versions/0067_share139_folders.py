"""share139_folders: the folders of the public shares of 139 Cloud the search found, each a card of the application.

A card of 139 Cloud is a folder of a public share. The search of the application finds the shares published in public indexes;
each folder it offers gets a number here, and that number is the card in the address of a source (`app://share139/<card>`),
so a chosen card is read again after a restart without the search. The numbers of the owner's own shares in the profile are
small; the numbers given here begin at 1000000.
"""
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE share139_folders (
            card INTEGER PRIMARY KEY,
            link_id TEXT NOT NULL,
            folder_id TEXT NOT NULL,
            password TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            publisher_url TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            found_by TEXT NOT NULL DEFAULT '',
            found_at TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            UNIQUE (link_id, folder_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE share139_folders")
