"""planner: the table of the asset of every video series (stage 12, the table of an episode).

`plan_assets` holds a container of the library as the planner reads it (core/plan/files.container_json): its video with
the provenance of the row it came from, its tracks with theirs and the video they are aligned to. `plan_failures` are the
attempts that failed, `plan_commands` the commands «Удалить» that wait, `plan_runs` the syncs under way.

The asset of the video series is carried over from what the service recorded: every ready file of the plan
(`media_items`) with the voice-overs placed in it (`voice_dubs`). A track keeps the key its file's tags give it (the
voice-over: `ST_DUB`), so the planner finds it in the files made before it.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def _row(source_id, row_key, video=""):
    return {"source_id": int(source_id) if source_id is not None else -1, "row_key": row_key or "", "video": video or ""}


def _snapshot(player, group, height, link="", label=""):
    return {"source": "", "player": player or "?", "group": group, "height": int(height or 0), "link": link or "",
            "label": label or ""}


def upgrade() -> None:
    op.execute("CREATE TABLE plan_assets (series_id INTEGER NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL, "
               "PRIMARY KEY (series_id, name))")
    op.execute("CREATE TABLE plan_failures (series_id INTEGER NOT NULL, key TEXT NOT NULL, PRIMARY KEY (series_id, key))")
    op.execute("CREATE TABLE plan_commands (series_id INTEGER NOT NULL, command TEXT NOT NULL, "
               "PRIMARY KEY (series_id, command))")
    op.execute("CREATE TABLE plan_runs (series_id INTEGER PRIMARY KEY, download INTEGER NOT NULL, started_at TEXT)")
    conn = op.get_bind()
    rows = {}
    for row in conn.execute(sa.text("SELECT series_id, source_id, row_key, video, player, voice_group, voice_label, "
                                    "season, episode_start, episode_end, excluded FROM composition_rows")).mappings():
        rows[(row["series_id"], row["source_id"], row["row_key"])] = dict(row)
    items = conn.execute(sa.text(
        "SELECT m.* FROM media_items m WHERE NOT EXISTS (SELECT 1 FROM series_sources t WHERE t.series_id = m.series_id "
        "AND t.source_type = 'torrent') "
        "AND m.status = 'completed' AND m.final_filename IS NOT NULL AND m.plan_status = 'in_plan_single'")).mappings()
    dubs: dict[tuple, list[dict]] = {}
    for dub in conn.execute(sa.text("SELECT * FROM voice_dubs WHERE status = 'placed'")).mappings():
        dubs.setdefault((dub["series_id"], dub["season"], dub["episode"], dub["part"] or 0), []).append(dict(dub))
    for item in items:
        series_id = item["series_id"]

        def row_id(source_id, row_key):
            known = rows.get((series_id, source_id, row_key))
            return _row(source_id, row_key, known["video"] if known else "")

        slot = [item["season"], item["episode_start"]] + ([item["part"]] if item["part"] is not None else [])
        own = row_id(item["source_id"], item["row_key"])
        known = rows.get((series_id, item["source_id"], item["row_key"]))
        snapshot = _snapshot(known["player"] if known else None, item["voiceover_tag"], item["resolution"],
                             item["source_url"], known["voice_label"] if known else "")
        cut = json.loads(item["cut"]) if item["cut"] else None
        if cut:
            match = next((each for key, each in rows.items() if key[0] == series_id and key[1] == cut.get("source_id")
                          and each["episode_start"] == cut.get("start") and each["episode_end"] == cut.get("end")
                          and not each["excluded"]), None)
            compilation = (_row(match["source_id"], match["row_key"], match["video"]) if match
                           else _row(cut.get("source_id"), cut.get("key", "")))
            video = {"id": compilation, "snapshot": _snapshot(cut.get("player"), cut.get("voice_group"), cut.get("height")),
                     "episode": slot, "timeline": own, "cut_of": compilation}
        else:
            video = {"id": own, "snapshot": snapshot, "episode": slot, "timeline": own, "cut_of": None}
        tracks = []
        for dub in sorted(dubs.get((series_id, item["season"], item["episode_start"], item["part"] or 0), []),
                          key=lambda each: each["id"]):
            chain = json.loads(dub["chain"] or "[]")
            if not chain:
                continue
            candidate = chain[min(dub["attempt"], len(chain) - 1)]
            source, _, key = (dub["aligned_to"] or "").partition(":")
            aligned = own if (source, key) == (str(item["source_id"]), item["row_key"]) or not dub["aligned_to"] \
                else _row(int(source) if source.isdigit() else -1, key)
            tracks.append({"dub": row_id(candidate.get("source_id"), candidate.get("row_key") or candidate.get("url")),
                           "snapshot": _snapshot(candidate.get("player"), dub["voice_label"], 720, candidate.get("url")),
                           "episode": slot, "aligned_to": aligned, "tag": dub["voice_key"]})
        data = {"name": item["final_filename"], "off": bool(item["is_ignored_by_user"]), "video": video, "tracks": tracks}
        conn.execute(sa.text("INSERT OR REPLACE INTO plan_assets (series_id, name, data) VALUES (:s, :n, :d)"),
                     {"s": series_id, "n": item["final_filename"], "d": json.dumps(data, ensure_ascii=False)})


def downgrade() -> None:
    for table in ("plan_runs", "plan_commands", "plan_failures", "plan_assets"):
        op.execute(f"DROP TABLE {table}")
