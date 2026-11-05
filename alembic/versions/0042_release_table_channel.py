"""sources: the channel of a page joins its table, and the line of the constructor after the host of the page.

`release_tables.channel` is the channel the page belongs to; the tables of the VK channels take it from their address
(`https://vkvideo.ru/@<channel>|<query>`), any other page has none. The line reads `PID:vkvideo.ru | mir.donghua | P:VK | …`;
the rules written for the line without it are fitted by the next revision (rules).
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE release_tables ADD COLUMN channel TEXT NOT NULL DEFAULT ''")
    op.execute("UPDATE release_tables SET channel = substr(url, length('https://vkvideo.ru/@') + 1, "
               "instr(url, '|') - length('https://vkvideo.ru/@') - 1) "
               "WHERE site = 'vk_channel' AND url LIKE 'https://vkvideo.ru/@%|%'")


def downgrade() -> None:
    op.execute("ALTER TABLE release_tables DROP COLUMN channel")
