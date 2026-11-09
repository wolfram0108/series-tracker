"""planner: the voice-overs the service placed beside a video and never packed into its container.

The asset was carried over (0052) with every placed voice-over as a track inside its container; a voice-over placed with
a file of its own (`voice_dubs.output_path`) was packed later by a queue that is gone, and some never were. Such a track
names its file (relative to the folder of the series): the next build takes the sound from it and removes it. A track
whose file is not there any more is not in the container either: it leaves the table, and the planner makes it anew.
"""
import json
import os

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    beside: dict[tuple, str] = {}
    for dub in conn.execute(sa.text(
            "SELECT d.series_id, d.season, d.episode, d.part, d.voice_key, d.output_path, s.save_path FROM voice_dubs d "
            "JOIN series s ON s.id = d.series_id WHERE d.status = 'placed' AND d.output_path IS NOT NULL "
            "AND d.output_path != ''")).mappings():
        slot = (dub["season"], dub["episode"]) + ((dub["part"],) if dub["part"] else ())
        beside[(dub["series_id"], slot, dub["voice_key"])] = os.path.relpath(dub["output_path"], dub["save_path"] or "/")
    if not beside:
        return
    roots = {row["id"]: row["save_path"] for row in conn.execute(sa.text("SELECT id, save_path FROM series")).mappings()}
    for asset in conn.execute(sa.text("SELECT series_id, name, data FROM plan_assets")).mappings().all():
        data = json.loads(asset["data"])
        slot = tuple(data["video"]["episode"])
        tracks, changed = [], False
        for track in data["tracks"]:
            name = beside.get((asset["series_id"], slot, track.get("tag")))
            if name is None:
                tracks.append(track)
                continue
            changed = True
            if os.path.exists(os.path.join(roots.get(asset["series_id"]) or "/", name)):
                tracks.append({**track, "file": name})
        if changed:
            conn.execute(sa.text("UPDATE plan_assets SET data = :d WHERE series_id = :s AND name = :n"),
                         {"d": json.dumps({**data, "tracks": tracks}, ensure_ascii=False), "s": asset["series_id"],
                          "n": asset["name"]})


def downgrade() -> None:
    conn = op.get_bind()
    for asset in conn.execute(sa.text("SELECT series_id, name, data FROM plan_assets")).mappings().all():
        data = json.loads(asset["data"])
        if any("file" in track for track in data["tracks"]):
            tracks = [{key: value for key, value in track.items() if key != "file"} for track in data["tracks"]]
            conn.execute(sa.text("UPDATE plan_assets SET data = :d WHERE series_id = :s AND name = :n"),
                         {"d": json.dumps({**data, "tracks": tracks}, ensure_ascii=False), "s": asset["series_id"],
                          "n": asset["name"]})
