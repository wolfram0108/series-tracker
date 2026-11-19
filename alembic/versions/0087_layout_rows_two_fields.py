"""composition: the rows of the layout of a series are kept as two fields of its record (composition_state.rows — the rows
of sound — and .subtitles, each the JSON of their models in the order of the layout) instead of a row of composition_rows
each; composition_rows goes. Ten thousand rows of a series are read and written as one value each.
"""
import json

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

_COLUMNS = ("series_id, source_id, row_key, excluded, exclusion, season, episode_start, episode_end, part, voice_group, "
            "voice_label, voice_name, voice_unknown, player, player_label, grp, text, heights, state, published, link, "
            "video, timeline, track, language, subtitle_type, stale")

_COMPOSITION_ROWS = (
    "CREATE TABLE composition_rows ( series_id INTEGER NOT NULL, source_id INTEGER NOT NULL, row_key TEXT NOT NULL,"
    " seq INTEGER NOT NULL, excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)), exclusion TEXT CHECK"
    " (exclusion IN ('rule', 'no_number', 'source', 'settings')), season INTEGER, episode_start INTEGER, episode_end INTEGER,"
    " part INTEGER, voice_group TEXT, voice_label TEXT NOT NULL DEFAULT '', voice_name TEXT NOT NULL DEFAULT '',"
    " voice_unknown INTEGER, player TEXT NOT NULL, player_label TEXT NOT NULL DEFAULT '', grp TEXT NOT NULL DEFAULT '',"
    " text TEXT NOT NULL DEFAULT '', heights TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL CHECK (state IN"
    " ('playable', 'absent', 'not_obtained')), published TEXT, link TEXT NOT NULL DEFAULT '', video TEXT NOT NULL DEFAULT '',"
    " stale INTEGER NOT NULL DEFAULT 0, timeline TEXT NOT NULL DEFAULT '', track TEXT NOT NULL DEFAULT 'sound',"
    " language TEXT NOT NULL DEFAULT '', subtitle_type TEXT NOT NULL DEFAULT '', PRIMARY KEY (series_id, source_id, row_key),"
    " UNIQUE (series_id, seq))")


def row_of(record) -> dict:
    """A row of composition_rows as the JSON of its model (core/contracts/composition.CompositionRow), its fields in their
    order and `in_output` as the model tells it."""
    (series_id, source_id, row_key, excluded, exclusion, season, episode_start, episode_end, part, voice_group, voice_label,
     voice_name, voice_unknown, player, player_label, group, text, heights, state, published, link, video, timeline, track,
     language, subtitle_type, stale) = record
    return {"series_id": series_id, "source_id": source_id, "row_key": row_key, "excluded": bool(excluded),
            "exclusion": exclusion, "season": season, "episode_start": episode_start, "episode_end": episode_end,
            "part": part, "voice_group": voice_group, "voice_label": voice_label, "voice_name": voice_name,
            "voice_unknown": voice_unknown, "player": player, "player_label": player_label, "group": group, "text": text,
            "heights": json.loads(heights), "state": state, "published": published, "link": link, "video": video,
            "timeline": timeline, "track": track, "language": language, "subtitle_type": subtitle_type,
            "stale": bool(stale),
            "in_output": (not excluded and season is not None and episode_start is not None
                          and (voice_group is not None or track == "subtitles"))}


def _text(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()


def upgrade() -> None:
    conn = op.get_bind()
    op.execute("ALTER TABLE composition_state ADD COLUMN rows BLOB")
    op.execute("ALTER TABLE composition_state ADD COLUMN subtitles BLOB")
    for (series_id,) in conn.exec_driver_sql("SELECT DISTINCT series_id FROM composition_rows").fetchall():
        rows = [row_of(each) for each in conn.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM composition_rows WHERE series_id = ? ORDER BY seq", (series_id,)).fetchall()]
        conn.exec_driver_sql(
            "INSERT INTO composition_state (series_id, rows, subtitles) VALUES (?, ?, ?) ON CONFLICT (series_id) DO UPDATE"
            " SET rows = excluded.rows, subtitles = excluded.subtitles",
            (series_id, _text([each for each in rows if each["track"] == "sound"]),
             _text([each for each in rows if each["track"] == "subtitles"])))
    op.execute("DROP TABLE composition_rows")


def downgrade() -> None:
    conn = op.get_bind()
    op.execute(_COMPOSITION_ROWS)
    names = [each.strip() for each in _COLUMNS.split(",")]
    for series_id, rows, subtitles in conn.exec_driver_sql(
            "SELECT series_id, rows, subtitles FROM composition_state WHERE rows IS NOT NULL").fetchall():
        for seq, row in enumerate([*json.loads(rows), *json.loads(subtitles or "[]")]):
            values = {**row, "grp": row["group"], "excluded": int(row["excluded"]), "stale": int(row["stale"]),
                      "heights": json.dumps(row["heights"])}
            conn.exec_driver_sql(f"INSERT INTO composition_rows (seq, {_COLUMNS}) VALUES ({', '.join('?' * 28)})",
                                 (seq, *[values[name] for name in names]))
    op.execute("ALTER TABLE composition_state DROP COLUMN subtitles")
    op.execute("ALTER TABLE composition_state DROP COLUMN rows")
