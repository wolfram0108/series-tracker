"""sources: a counter of the writes of every page (release_tables.writes): every write of its record takes the next
number of the module (release_writes, one row, never going back), so a page never has a number twice; composition: the print of the inputs each layout was counted from
(composition_state.inputs). A start counts again only the layouts whose inputs are not the ones they were counted from.
"""
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE release_tables ADD COLUMN writes INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE TABLE release_writes (last INTEGER NOT NULL)")
    op.execute("INSERT INTO release_writes (last) VALUES (0)")
    op.execute("ALTER TABLE composition_state ADD COLUMN inputs TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE composition_state DROP COLUMN inputs")
    op.execute("DROP TABLE release_writes")
    op.execute("ALTER TABLE release_tables DROP COLUMN writes")
