"""sources: the rows of the last complete table of a page are kept as one field of its record (release_tables.rows, the
JSON of their models, in the order of the table) instead of a row of release_rows each; release_rows goes. A table of
thousands of rows is read and given on as one value.
"""
import json

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None

# The fields of a row of a table (core/contracts/release_table.TableRow) in the order of the model, from the columns of
# release_rows.
_COLUMNS = ('"key", player, player_label, grp, season, episode, text, voice_name, voice_kind, heights, state, reason, '
            "duration_s, published, link, video, timeline, track, language, subtitle_type, site_fields, part")

_RELEASE_ROWS = (
    "CREATE TABLE release_rows ( url TEXT NOT NULL REFERENCES release_tables (url) ON DELETE CASCADE, seq INTEGER NOT NULL,"
    ' "key" TEXT NOT NULL, player TEXT NOT NULL, player_label TEXT NOT NULL DEFAULT \'\', grp TEXT NOT NULL DEFAULT \'\','
    " text TEXT NOT NULL DEFAULT '', voice_name TEXT NOT NULL DEFAULT '', voice_kind TEXT NOT NULL DEFAULT '',"
    " heights TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL CHECK (state IN ('playable', 'absent', 'not_obtained')),"
    " reason TEXT, duration_s REAL, published TEXT, link TEXT NOT NULL DEFAULT '', site_fields TEXT NOT NULL DEFAULT '{}',"
    " video TEXT NOT NULL DEFAULT '', part TEXT NOT NULL DEFAULT '', timeline TEXT NOT NULL DEFAULT '',"
    " track TEXT NOT NULL DEFAULT 'sound', language TEXT NOT NULL DEFAULT '', subtitle_type TEXT NOT NULL DEFAULT '',"
    " season TEXT NOT NULL DEFAULT '', episode TEXT NOT NULL DEFAULT '', PRIMARY KEY (url, \"key\"), UNIQUE (url, seq))")


def row_of(record) -> dict:
    """A row of release_rows as the JSON of its model."""
    (key, player, player_label, group, season, episode, text, voice_name, voice_kind, heights, state, reason, duration_s,
     published, link, video, timeline, track, language, subtitle_type, site_fields, part) = record
    return {"key": key, "player": player, "player_label": player_label, "group": group, "season": season,
            "episode": episode, "text": text, "voice": {"name": voice_name, "kind": voice_kind},
            "heights": json.loads(heights), "state": state, "reason": reason, "duration_s": duration_s,
            "published": published, "link": link, "video": video, "timeline": timeline, "track": track,
            "language": language, "subtitle_type": subtitle_type, "site_fields": json.loads(site_fields), "part": part}


def upgrade() -> None:
    conn = op.get_bind()
    op.execute("ALTER TABLE release_tables ADD COLUMN rows BLOB")
    for (url,) in conn.exec_driver_sql("SELECT url FROM release_tables WHERE complete = 1").fetchall():
        rows = [row_of(each) for each in conn.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM release_rows WHERE url = ? ORDER BY seq", (url,)).fetchall()]
        conn.exec_driver_sql("UPDATE release_tables SET rows = ? WHERE url = ?",
                             (json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(), url))
    op.execute("DROP TABLE release_rows")


def downgrade() -> None:
    conn = op.get_bind()
    op.execute(_RELEASE_ROWS)
    for url, rows in conn.exec_driver_sql("SELECT url, rows FROM release_tables WHERE rows IS NOT NULL").fetchall():
        for seq, row in enumerate(json.loads(rows)):
            conn.exec_driver_sql(
                f"INSERT INTO release_rows (url, seq, {_COLUMNS}) VALUES ({', '.join('?' * 24)})",
                (url, seq, row["key"], row["player"], row["player_label"], row["group"], row["season"], row["episode"],
                 row["text"], row["voice"]["name"], row["voice"]["kind"], json.dumps(row["heights"]), row["state"],
                 row["reason"], row["duration_s"], row["published"], row["link"], row["video"], row["timeline"],
                 row["track"], row["language"], row["subtitle_type"], json.dumps(row["site_fields"], ensure_ascii=False),
                 row["part"]))
    op.execute("ALTER TABLE release_tables DROP COLUMN rows")
