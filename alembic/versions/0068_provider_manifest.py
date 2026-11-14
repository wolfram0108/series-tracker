"""provider_manifest: the last manifest of the provider of the socket (contracts/provider_socket.md), by its address.

The module `sources` registers the sites of this manifest at its start, before the provider is reached, so that a provider that
is down takes no page of the tracker's tables out of its sites' hands. The manifest is the provider's own text, kept as it came.
"""
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE provider_manifest (
            url TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            revision TEXT NOT NULL,
            body TEXT NOT NULL,
            saved_at TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE provider_manifest")
