"""The records of the planner as they are stored: the table of the asset as JSON and back (the database and the attachment of every
container agree), the key a track has in the tags of its container, and the keys of the failed attempts and of the commands
«Удалить». Pure: the module of the planner stores and reads them, the build of a container writes the table into the file."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .tables import Container, RowId, Snapshot, Subtitle, Track, Video


def row_json(row: RowId | None):
    return None if row is None else asdict(row)


def container_json(container: Container) -> dict:
    return {"name": container.name, "off": container.off, "video": {
        "id": asdict(container.video.id), "snapshot": asdict(container.video.snapshot),
        "episode": list(container.video.episode), "timeline": asdict(container.video.timeline),
        "cut_of": row_json(container.video.cut_of),
        **({"timeline_snapshot": asdict(container.video.timeline_snapshot)} if container.video.timeline_snapshot else {})},
        "tracks": [{"dub": asdict(track.dub), "snapshot": asdict(track.snapshot), "episode": list(track.episode),
                    "aligned_to": asdict(track.aligned_to), **({"tag": track.tag} if track.tag else {}),
                    **({"file": track.file} if track.file else {})}
                   for track in container.tracks], **({"own_at": container.own_at} if container.own_at else {}),
        **({"titles": dict(container.titles)} if container.titles else {}),
        **({"languages": dict(container.languages)} if container.languages else {}),
        **({"subtitles": [{**asdict(each), "row": asdict(each.row), "timeline": asdict(each.timeline)}
                          for each in container.subtitles]} if container.subtitles else {})}


def container_of(data: dict) -> Container:
    video = data["video"]
    return Container(data["name"], Video(RowId(**video["id"]), Snapshot(**video["snapshot"]), tuple(video["episode"]),
                                         RowId(**video["timeline"]), RowId(**video["cut_of"]) if video["cut_of"] else None,
                                         Snapshot(**video["timeline_snapshot"]) if video.get("timeline_snapshot") else None),
                     tuple(Track(RowId(**track["dub"]), Snapshot(**track["snapshot"]), tuple(track["episode"]),
                                 RowId(**track["aligned_to"]), track.get("tag", ""), track.get("file", ""))
                           for track in data["tracks"]),
                     data["off"], data.get("own_at", 0), tuple(sorted((data.get("titles") or {}).items())),
                     tuple(sorted((data.get("languages") or {}).items())),
                     tuple(Subtitle(**{**each, "row": RowId(**each["row"]), "timeline": RowId(**each["timeline"])})
                           for each in data.get("subtitles") or ()))


def dub_key(dub: RowId) -> str:
    """The key of a track in its container's tags: the row it came from."""
    return f"{dub.source_id}:{dub.row_key}:{dub.video}"


def key_in(container: Container, dub: RowId) -> str:
    """The key a track of a container has in the file's tags."""
    track = next((each for each in container.tracks if each.dub == dub), None)
    return track.tag if track is not None and track.tag else dub_key(dub)


def containers_json(containers: Iterable[Container]) -> list[dict]:
    """The table of the asset of a series as it is written: one record for each file."""
    return [container_json(each) for each in containers]


def key_of_row(row: RowId) -> list:
    return [row.source_id, row.row_key, row.video]


def stored_command(payload: dict) -> list:
    """The command «Удалить» of the window as it is stored: its kind and the file, and for a track the row of its voice-over."""
    command = [payload["kind"], payload["name"]]
    if payload["kind"] == "track":
        dub = payload["dub"]
        command.append([dub["source_id"], dub["row_key"], dub.get("video", "")])
    return command


def command_of_key(key: list) -> tuple:
    return (key[0], key[1], RowId(*key[2])) if key[0] == "track" else (key[0], key[1])


def key_of_command(command: tuple) -> list:
    return [command[0], command[1], key_of_row(command[2])] if command[0] == "track" else [command[0], command[1]]


def failure_of_key(key: list) -> tuple:
    return (key[0], *(RowId(*each) for each in key[1:]))


def key_of_failure(key: tuple) -> list:
    return [key[0], *(key_of_row(each) for each in key[1:])]
