"""What the window shows of the plan of a series: the asset (every file with the provenance of its video and tracks, and
whether its row is online), the goal of every episode as the last plan counted it, the failures and the commands."""
from __future__ import annotations

from dataclasses import asdict

from core.plan.executor import SeriesSync
from core.plan.planner import Plan
from core.plan.tables import RowId


def _row(row: RowId | None) -> dict | None:
    return None if row is None else asdict(row)


def view_of(series_id: int, sync: SeriesSync, made: Plan | None, *, running: bool,
            online: frozenset[RowId] | None = None) -> dict:
    assets = []
    for container in sorted(sync.containers.values(), key=lambda each: each.name):
        video = container.video
        assets.append({
            "name": container.name, "off": container.off, "episode": list(video.episode),
            "video": {"row": _row(video.id), "cut_of": _row(video.cut_of), **asdict(video.snapshot),
                      "online": None if online is None else video.id in online},
            "tracks": [{"row": _row(track.dub), **asdict(track.snapshot),
                        "online": None if online is None else track.dub in online} for track in container.tracks]})
    episodes = []
    if made is not None:
        for goal in made.goals:
            episodes.append({
                "slot": list(goal.slot), "name": goal.name, "off": goal.off,
                "video": {"kind": goal.video.kind, **asdict(goal.video.snapshot), "row": _row(goal.video.timeline),
                          "file": goal.video.container.name if goal.video.container is not None else None},
                "tracks": [{"voice": way.voice, "kind": way.kind, "row": _row(way.dub), **asdict(way.snapshot),
                            "own": way.own} for way in goal.tracks]})
    failures = [{"kind": key[0], "row": _row(key[1]), "video": _row(key[2]) if len(key) > 2 else None}
                for key in sorted(sync.failed, key=repr)]
    commands = [{"kind": each[0], "name": each[1], "dub": _row(each[2]) if len(each) > 2 else None}
                for each in sorted(sync.commands, key=repr)]
    return {"series_id": series_id, "running": running, "assets": assets, "episodes": episodes,
            "failures": failures, "commands": commands}
