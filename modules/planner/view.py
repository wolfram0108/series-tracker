"""What the window shows of the plan of a series: the asset (every file with the provenance of its video and tracks, and
whether its row is online), the goal of every episode as the last plan counted it, the failures and the commands."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from core.plan.executor import SeriesSync
from core.plan.planner import Plan, voice_of
from core.plan.tables import OnlineRow, RowId


def _row(row: RowId | None) -> dict | None:
    return None if row is None else asdict(row)


def _slots(row: OnlineRow) -> list[list[int]]:
    if row.part is not None:
        return [[row.season, row.start, row.part]]
    return [[row.season, episode] for episode in range(row.start, row.end + 1)]


def covered(failure: dict, assets: list[dict]) -> bool:
    """A failed attempt the series does not lack: every episode of its row has a file in the library that holds what the
    attempt was for — its voice-over, as a track or as the own sound of the video (another source gave it), or the video
    when the row names no voice-over. A row no longer online names no episode and leaves nothing lacking."""
    for slot in failure["episodes"]:
        held = [each for each in assets if not each["off"] and tuple(each["episode"]) == tuple(slot)]
        if not held:
            return False
        voice = failure["voice"]
        if voice is not None and not any(each["video"].get("group") == voice
                                         or any(track.get("group") == voice for track in each["tracks"]) for each in held):
            return False
    return True


def placed(failed: Iterable[tuple], rows: Iterable[OnlineRow] = ()) -> list[dict]:
    """The failed attempts (`key`) with where each belongs, as the layout the rows are of has it: the episodes its row gives
    and its voice-over group; empty when the row is offline."""
    by_id: dict[RowId, list[OnlineRow]] = {}
    for row in rows:
        by_id.setdefault(row.id, []).append(row)
    found = []
    for key in sorted(failed, key=repr):
        held = by_id.get(key[1], [])
        found.append({"key": key, "kind": key[0], "row": _row(key[1]), "video": _row(key[2]) if len(key) > 2 else None,
                      "episodes": sorted({tuple(slot) for row in held for slot in _slots(row)}),
                      "voice": next((row.group for row in held if row.group), None)})
    return found


def view_of(series_id: int, sync: SeriesSync, made: Plan | None, *, running: bool,
            online: frozenset[RowId] | None = None, rows: Iterable[OnlineRow] = ()) -> dict:
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
                "slot": list(goal.slot), "name": goal.name, "off": goal.off, "voices_off": sorted(goal.voices_off),
                "video": {"kind": goal.video.kind, **asdict(goal.video.snapshot), "row": _row(goal.video.timeline),
                          "file": goal.video.container.name if goal.video.container is not None else None,
                          "cut_of": _row(goal.video.row.id if goal.video.kind == "cut" else
                                         goal.video.container.video.cut_of if goal.video.container is not None else None)},
                "tracks": [{"voice": way.voice, "kind": way.kind, "row": _row(way.dub), **asdict(way.snapshot),
                            "own": way.own} for way in goal.tracks],
                "markup": voice_of(goal.markup.group, goal.markup.id) if goal.markup is not None else None})
    failures = [{key: value for key, value in each.items() if key != "key"} for each in placed(sync.failed, rows)]
    for failure in failures:
        failure["covered"] = covered(failure, assets)
    commands = [{"kind": each[0], "name": each[1], "dub": _row(each[2]) if len(each) > 2 else None}
                for each in sorted(sync.commands, key=repr)]
    return {"series_id": series_id, "running": running, "assets": assets, "episodes": episodes,
            "failures": failures, "commands": commands}
