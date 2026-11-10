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
from .planner import Goal, Group, Plan, video_op
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
                    "aligned_to": asdict(track.aligned_to), **({"tag": track.tag} if track.tag else {}),
                    **({"file": track.file} if track.file else {})}
                   for track in container.tracks], **({"own_at": container.own_at} if container.own_at else {}),
        **({"titles": dict(container.titles)} if container.titles else {}),
        **({"languages": dict(container.languages)} if container.languages else {})}


def container_of(data: dict) -> Container:
    video = data["video"]
    return Container(data["name"], Video(RowId(**video["id"]), Snapshot(**video["snapshot"]), tuple(video["episode"]),
                                         RowId(**video["timeline"]), RowId(**video["cut_of"]) if video["cut_of"] else None),
                     tuple(Track(RowId(**track["dub"]), Snapshot(**track["snapshot"]), tuple(track["episode"]),
                                 RowId(**track["aligned_to"]), track.get("tag", ""), track.get("file", ""))
                           for track in data["tracks"]),
                     data["off"], data.get("own_at", 0), tuple(sorted((data.get("titles") or {}).items())),
                     tuple(sorted((data.get("languages") or {}).items())))


def label_of(slot) -> str:
    return f"s{slot[0]:02d}e{slot[1]:02d}" + (f"pt{slot[2]}" if len(slot) > 2 else "")


def dub_key(dub: RowId) -> str:
    """The key of a track in its container's tags: the row it came from."""
    return f"{dub.source_id}:{dub.row_key}:{dub.video}"


def key_in(container: Container, dub: RowId) -> str:
    """The key a track of a container has in the file's tags."""
    track = next((each for each in container.tracks if each.dub == dub), None)
    return track.tag if track is not None and track.tag else dub_key(dub)


async def as_in_file(files: "Library", container: Container) -> Container:
    """A file made before the planner (its tracks keyed by the voice-over, `tag`) is told by the table in an order of its
    own: the order of its tracks is the order of the file, read from its tags, so that the table is the file."""
    if not any(track.tag for track in container.tracks):
        return container
    held = await mkv.contents(files.path(container.name))
    if held is None:
        return container
    place = {each.key: number for number, each in enumerate(held.audio) if each.key}
    own_at = next((sum(1 for other in held.audio[:number] if not other.own) for number, each in enumerate(held.audio)
                   if each.own), 0)
    return replace(container, tracks=tuple(sorted(container.tracks, key=lambda track: place.get(track.tag or "", len(place)))),
                   own_at=own_at)



class BuildFailed(RuntimeError):
    """A container that could not be built or read back as built: the file and the problem (core/container.py); `code` is
    the problem without the words of the program that come after its colon."""

    def __init__(self, name: str, problem: str) -> None:
        super().__init__(f"{name}: {problem}")
        self.name, self.code = name, problem.split(":", 1)[0]

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

    async def source_of(self, container: Container, dub: RowId, own: bool) -> tuple[str, int]:
        """Where the sound of a track is: its own file beside the container (placed, never packed), else its stream in
        the container."""
        track = None if own else next((each for each in container.tracks if each.dub == dub), None)
        if track is not None and track.file:
            return self.path(track.file), 0
        return self.path(container.name), await self.stream_of(container, dub, own)

    async def stream_of(self, container: Container, dub: RowId, own: bool) -> int:
        """The index of the audio stream of a track in a container, told by the container's own tags."""
        held = await mkv.contents(self.path(container.name))
        key = key_in(container, dub)
        for track in held.audio if held is not None else ():
            if (own and track.own) or (not own and track.key == key):
                return track.index
        raise FileNotFoundError(f"{container.name} holds no track {dub} (own={own})")

    def video_path(self, goal: Goal, results: dict[str, Made]) -> str:
        if goal.video.kind == "asset":
            return self.path(goal.video.container.name)
        return results[video_op(goal)].file

    async def build(self, made: Plan, group: Group, results: dict[str, Made], report_for=None) -> list[tuple]:
        """The containers a group builds anew, aside, each with its provenance; the steps of the journal of the group.
        `report_for(goal)` gives the callable a build of that goal tells its progress to (core.container.Report)."""
        steps: list[tuple] = []
        for slot in group.built:
            goal = made.goal(slot)
            video = video_of(goal, results)
            video_path = self.video_path(goal, results)
            identity = mkv.identity(video.timeline.source_id, video.timeline.row_key)
            own_stream = await self.stream_of(goal.video.container, video.id, True) if goal.video.kind == "asset" else 0
            titles, languages = dict(goal.titles), dict(goal.languages)
            # A video without a group is only that of a file made of a row of no group, which the plan takes no more.
            label = video.snapshot.group or "—"
            sound = mkv.AudioIn(label, video_path, own_stream, identity, own=True, title=titles.get(label, ""),
                                language=languages.get(label, ""))
            tracks, made_tracks, packed = [], [], []
            for way in goal.tracks:
                if way.kind == "reuse":
                    path, stream = await self.source_of(way.container, way.dub, way.own)
                    made_tracks.append(Track(way.dub, way.snapshot, slot, video.timeline))
                    if path != self.path(way.container.name):
                        packed.append(os.path.relpath(path, self.root))
                elif way.kind == "timeline":  # the sound of the download of the timeline: in step with the video as it is
                    path, stream = results[f"row:{way.row.id}"].file, 0
                    made_tracks.append(Track(way.dub, way.snapshot, slot, video.timeline))
                else:
                    aligned = results[f"align:{slot}:{way.voice}"]
                    path, stream = aligned.file, 0
                    made_tracks.append(aligned.value)
                label = way.snapshot.group or way.voice
                tracks.append(mkv.AudioIn(label, path, stream, identity, key=dub_key(way.dub), title=titles.get(label, ""),
                                          language=languages.get(label, "")))
            tracks.insert(goal.own_at, sound)  # the own sound of the video in the place the order of the season gives it
            built = Container(goal.name, replace(video, episode=slot), tuple(made_tracks), goal.off, goal.own_at,
                              goal.titles, goal.languages)
            staging = self.staging(goal.name)
            os.makedirs(os.path.dirname(staging), exist_ok=True)
            report = report_for(goal) if report_for is not None else None
            problem = await mkv.build(staging, video_path, identity, tracks, provenance=container_json(built),
                                      **({"report": report} if report is not None else {}))
            if problem is not None:
                raise BuildFailed(goal.name, problem)
            if report is not None:
                await report("built", {})
            steps.append(("publish", os.path.relpath(staging, self.root), goal.name))
            steps += [("remove", name) for name in packed]  # the file now holds the track
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
                tracks = [mkv.AudioIn(each.label, path, each.index, each.aligned_to, own=each.own, key=each.key,
                                      title=each.title if each.title != each.label else "",
                                      language=each.language if each.language != mkv.UNSTATED else "")
                          for each in held.audio]
                problem = await mkv.build(again, path, held.video, tracks, provenance=table[step[1]])
                if problem is not None:
                    raise BuildFailed(step[1], problem)
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
