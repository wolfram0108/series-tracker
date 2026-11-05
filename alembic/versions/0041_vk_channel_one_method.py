"""A VK channel is read one way: the whole listing, then the query over the titles. The search mode leaves.

- catalog: `series_sources.vk_search_mode` is dropped;
- sources: the key of the table of a channel is `channel|query` (it was `channel|query|mode`). A key that had both modes
  keeps the table the composition points to; a channel with no query is no page of the site any more, its tables go;
- composition: `composition_sources.table_url` loses the mode as the key of the table did.
The tables keep their rows and their time of reading: the next scan reads them again by their term.
"""
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

MODES = ("search", "get_all")


def _strip(column: str, mode: str) -> str:
    return f"substr({column}, 1, length({column}) - {len(mode) + 1})"


def upgrade() -> None:
    op.execute("ALTER TABLE series_sources DROP COLUMN vk_search_mode")
    channel = "site = 'vk_channel'"
    gone = f"SELECT url FROM release_tables WHERE {channel} AND url LIKE '%||%'"
    op.execute(f"DELETE FROM release_rows WHERE url IN ({gone})")
    op.execute(f"DELETE FROM release_tables WHERE url IN ({gone})")
    for mode in MODES:
        other = MODES[1 - MODES.index(mode)]
        # Of the two tables of one address the one the composition does not point to goes.
        twin = (f"SELECT t.url FROM release_tables t WHERE t.{channel} AND t.url LIKE '%|{mode}' "
                f"AND EXISTS (SELECT 1 FROM release_tables o WHERE o.url = {_strip('t.url', mode)} || '|{other}') "
                f"AND NOT EXISTS (SELECT 1 FROM composition_sources c WHERE c.table_url = t.url)")
        op.execute(f"DELETE FROM release_rows WHERE url IN ({twin})")
        op.execute(f"DELETE FROM release_tables WHERE url IN ({twin})")
    for mode in MODES:
        for table, column, where in (("release_rows", "url", f"url IN (SELECT url FROM release_tables WHERE {channel})"),
                                     ("release_tables", "url", channel),
                                     ("composition_sources", "table_url", "table_url LIKE 'https://vkvideo.ru/@%'")):
            op.execute(f"UPDATE {table} SET {column} = {_strip(column, mode)} "
                       f"WHERE {column} LIKE '%|%|{mode}' AND {where}")


def downgrade() -> None:
    op.execute("ALTER TABLE series_sources ADD COLUMN vk_search_mode TEXT NOT NULL DEFAULT 'search'")
    channel = "site = 'vk_channel'"
    for table, column, where in (("release_rows", "url", f"url IN (SELECT url FROM release_tables WHERE {channel})"),
                                 ("release_tables", "url", channel),
                                 ("composition_sources", "table_url", "table_url LIKE 'https://vkvideo.ru/@%'")):
        op.execute(f"UPDATE {table} SET {column} = {column} || '|search' WHERE {where}")
