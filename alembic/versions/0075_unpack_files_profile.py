"""The rules of the tab «Файлы» are the source's own, as those of the other tabs of the torrent editor (stage 15): every page
has its profile of each tab, empty until the owner writes rules in it. The one preset for every source (setting
`unpack.files_profile_id`, profile «Торрент: файлы» with rules made by the module) leaves with its rules."""
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE unpack_sources ADD COLUMN files_profile_id INTEGER REFERENCES parser_profiles (id)")
    conn = op.get_bind()
    stored = conn.exec_driver_sql("SELECT value FROM settings WHERE key = 'unpack.files_profile_id'").fetchone()
    if stored and stored[0] and str(stored[0]).isdigit():
        preset = int(stored[0])
        conn.exec_driver_sql("DELETE FROM parser_rule_conditions WHERE rule_id IN "
                             "(SELECT id FROM parser_rules WHERE profile_id = ?)", (preset,))
        conn.exec_driver_sql("DELETE FROM parser_rules WHERE profile_id = ?", (preset,))
        conn.exec_driver_sql("DELETE FROM parser_profiles WHERE id = ? AND kind = 'torrent'", (preset,))
    conn.exec_driver_sql("DELETE FROM settings WHERE key = 'unpack.files_profile_id'")


def downgrade() -> None:
    op.execute("ALTER TABLE unpack_sources DROP COLUMN files_profile_id")
