"""The files of the plan of a series (stage 12): the table of the asset as JSON (the database and the attachment of every
container agree), the build of the container of a goal, and the journal that applies a sync to the library.

The journal: the new containers are built aside (staging), then the journal — the steps and the table they lead to — is
written, then carried out: out of the way, remove, into place, publish, retag. A stop at any step leaves the journal;
`recover` carries it out to the end. A stop before the journal leaves the snapshot as it was. Every step can be carried out
twice: it does what is left of it."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, replace

from core import container as mkv

from .executor import Made, video_of
from .planner import Goal, Group, Plan
from .tables import Container, RowId, Snapshot, Track, Video

JOURNAL = ".plan-journal.json"
STAGING = ".plan-staging"


def row_json(row: RowId | None):
    return None if row is None else asdict(row)


def container_json(container: Container) -> dict:
    return {"name": container.name, "off": container.off, "video": {
        "id": asdict(container.video.id), "snapshot": asdict(container.video.snapshot),
        "episode": list(container.video.episode), "timeline": asdict(container.video.timeline),
        "cut_of": row_json(container.video.cut_of)},
        "tracks": [{"dub": asdict(track.dub), "snapshot": asdict(track.snapshot), "episode": list(track.episode),
                    "aligned_to": asdict(track.aligned_to)} for track in container.tracks]}


def container_of(data: dict) -> Container:
    video = data["video"]
    return Container(data["name"], Video(RowId(**video["id"]), Snapshot(**video["snapshot"]), tuple(video["episode"]),
                                         RowId(**video["timeline"]), RowId(**video["cut_of"]) if video["cut_of"] else None),
                     tuple(Track(RowId(**track["dub"]), Snapshot(**track["snapshot"]), tuple(track["episode"]),
                                 RowId(**track["aligned_to"])) for track in data["tracks"]), data["off"])


def label_of(slot) -> str:
    return f"s{slot[0]:02d}e{slot[1]:02d}" + (f"pt{slot[2]}" if len(slot) > 2 else "")


def dub_key(dub: RowId) -> str:
    """The key of a track in its container's tags: the row it came from."""
    return f"{dub.source_id}:{dub.row_key}:{dub.video}"


class Library:
    """The folder of a series: its containers under their names, the staging folder and the journal."""

    def __init__(self, root: str) -> None:
        self.root = root

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    @property
    def journal(self) -> str:
        return os.path.join(self.root, JOURNAL)

    def staging(self, name: str) -> str:
        return os.path.join(self.root, STAGING, name.replace("/", "_") + ".building.mkv")

    async def stream_of(self, name: str, dub: RowId, own: bool) -> int:
        """The index of the audio stream of a track in a container, told by the container's own tags."""
        held = await mkv.contents(self.path(name))
        for track in held.audio:
            if (own and track.own) or (not own and track.key == dub_key(dub)):
                return track.index
        raise FileNotFoundError(f"{name} holds no track {dub} (own={own})")

    def video_path(self, goal: Goal, results: dict[str, Made]) -> str:
        if goal.video.kind == "asset":
            return self.path(goal.video.container.name)
        return results[f"video:{goal.slot}" if goal.video.kind == "download" else f"cut:{goal.slot}"].file

    async def build(self, made: Plan, group: Group, results: dict[str, Made]) -> list[tuple]:
        """The containers a group builds anew, aside, each with its provenance; the steps of the journal of the group."""
        steps: list[tuple] = []
        for slot in group.built:
            goal = made.goal(slot)
            video = video_of(goal, results)
            video_path = self.video_path(goal, results)
            identity = mkv.identity(video.timeline.source_id, video.timeline.row_key)
            own_stream = await self.stream_of(goal.video.container.name, video.id, True) if goal.video.kind == "asset" else 0
            tracks = [mkv.AudioIn(video.snapshot.group or "—", video_path, own_stream, identity, own=True)]
            made_tracks = []
            for way in goal.tracks:
                if way.kind == "reuse":
                    path = self.path(way.container.name)
                    stream = await self.stream_of(way.container.name, way.dub, way.own)
                    made_tracks.append(Track(way.dub, way.snapshot, slot, video.timeline))
                else:
                    aligned = results[f"align:{slot}:{way.voice}"]
                    path, stream = aligned.file, 0
                    made_tracks.append(aligned.value)
                tracks.append(mkv.AudioIn(way.snapshot.group or way.voice, path, stream, identity, key=dub_key(way.dub)))
            built = Container(goal.name, replace(video, episode=slot), tuple(made_tracks), goal.off)
            staging = self.staging(goal.name)
            os.makedirs(os.path.dirname(staging), exist_ok=True)
            problem = await mkv.build(staging, video_path, identity, tracks, provenance=container_json(built))
            if problem is not None:
                raise RuntimeError(f"{goal.name}: {problem}")
            steps.append(("publish", os.path.relpath(staging, self.root), goal.name))
        for slot, name in group.kept:
            goal = made.goal(slot)
            if goal.name != name:
                steps += [("move", name, goal.name), ("retag", goal.name)]
        steps += [("remove", name) for name in group.remove]
        for name, hidden in group.hide:
            steps += [("move", name, hidden), ("retag", hidden)]
        return steps

    def write_journal(self, steps: list[tuple], table: list[dict]) -> list[tuple]:
        """The order of the steps of a sync, written before any is carried out: out of the way first, then into place
        (two moves may swap names)."""
        moves = [step for step in steps if step[0] == "move"]
        ordered = [("move", old, old + ".moving") for _, old, _ in moves]
        ordered += [step for step in steps if step[0] == "remove"]
        ordered += [("move", old + ".moving", new) for _, old, new in moves]
        ordered += [step for step in steps if step[0] == "publish"]
        ordered += [step for step in steps if step[0] == "retag"]
        with open(self.journal + ".new", "w") as out:
            json.dump({"steps": ordered, "table": table}, out, ensure_ascii=False)
        os.replace(self.journal + ".new", self.journal)
        return ordered

    def read_journal(self) -> dict | None:
        if not os.path.exists(self.journal):
            return None
        with open(self.journal) as found:
            data = json.load(found)
        return {"steps": [tuple(step) for step in data["steps"]], "table": data["table"]}

    async def step(self, step: tuple, table: dict[str, dict]) -> None:
        """One step of the journal; carried out again after a stop, it does what is left of it."""
        if step[0] == "move":
            source, target = self.path(step[1]), self.path(step[2])
            if os.path.exists(source):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.replace(source, target)
        elif step[0] == "remove":
            if os.path.exists(self.path(step[1])):
                os.remove(self.path(step[1]))
        elif step[0] == "publish":
            staged = self.path(step[1])
            if os.path.exists(staged):
                os.makedirs(os.path.dirname(self.path(step[2])), exist_ok=True)
                os.replace(staged, self.path(step[2]))
        elif step[0] == "retag":
            # The file keeps its streams; its provenance follows its new name and state.
            path = self.path(step[1])
            if os.path.exists(path) and await mkv.provenance_of(path) != table.get(step[1]):
                again = path + ".retag"
                held = await mkv.contents(path)
                tracks = [mkv.AudioIn(each.label, path, each.index, each.aligned_to, own=each.own, key=each.key)
                          for each in held.audio]
                problem = await mkv.build(again, path, held.video, tracks, provenance=table[step[1]])
                if problem is not None:
                    raise RuntimeError(f"{step[1]}: {problem}")
                os.replace(again, path)

    def finish(self) -> None:
        """The journal is carried out: it goes, and what was made aside goes with it."""
        if os.path.exists(self.journal):
            os.remove(self.journal)
        shutil.rmtree(os.path.join(self.root, STAGING), ignore_errors=True)

    def files(self) -> list[str]:
        """The containers of the folder (names relative to it), the folders of the plan aside."""
        found = []
        for folder, dirs, names in os.walk(self.root):
            dirs[:] = [each for each in dirs if not each.startswith(".")]
            for name in names:
                if not name.startswith(".") and (name.endswith(".mkv") or name.endswith(".mkv.off")):
                    found.append(os.path.relpath(os.path.join(folder, name), self.root))
        return sorted(found)
