"""composition: a row whose player is switched off in the settings for every source is kept as excluded,
`exclusion='settings'`.

The order of sources holds which players are switched off for every source (`voices.players_off`); the rows of such a
player are a choice of the user, as a player switched off for one source is, and the layout says whose choice it is.
SQLite cannot widen a CHECK in place, so the table is built again from its own definition with only the list of the
reasons changed: every column and every other constraint stay as they are.

The downgrade takes these rows away: the earlier schema has no word for them.
"""
import re

from alembic import op
from sqlalchemy import text

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

_CHECK = "exclusion TEXT CHECK (exclusion IN ({}))"
_BEFORE = "'rule', 'no_number', 'source'"
_AFTER = "'rule', 'no_number', 'source', 'settings'"


def rebuild(bind, old: str, new: str, keep: str = "1") -> None:
    """The table of the rows of the layout with the reasons `new` in place of `old`, its rows kept where `keep` holds."""
    sql = bind.execute(text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'composition_rows'")).scalar()
    if _CHECK.format(old) not in sql:
        raise RuntimeError(f"composition_rows has no CHECK of the reasons {old}: {sql}")
    create = re.sub(r'^CREATE TABLE (IF NOT EXISTS )?"?composition_rows"?', "CREATE TABLE composition_rows_next",
                    sql.replace(_CHECK.format(old), _CHECK.format(new)))
    columns = ", ".join(f'"{row[1]}"' for row in bind.execute(text("PRAGMA table_info(composition_rows)")))
    bind.execute(text(create))
    bind.execute(text(f"INSERT INTO composition_rows_next ({columns}) SELECT {columns} FROM composition_rows WHERE {keep}"))
    bind.execute(text("DROP TABLE composition_rows"))
    bind.execute(text("ALTER TABLE composition_rows_next RENAME TO composition_rows"))


def upgrade() -> None:
    rebuild(op.get_bind(), _BEFORE, _AFTER)


def downgrade() -> None:
    rebuild(op.get_bind(), _AFTER, _BEFORE, "exclusion IS NULL OR exclusion != 'settings'")
