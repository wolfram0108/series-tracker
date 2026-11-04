"""The container of every episode of a video series, made at the end of a sync (ЭТАП_11).

The file of an episode is one MKV: the video of the reference, its own sound, and the voice-overs in the order of the
season. Packing is the last step of a run of a sync (scan asks `downloads.pack` when every other work of the run has
ended), so the disk changes in a sync alone (И-59) and the old container lives until the new one is whole (ЭТАП_11,
answer 3): the new one is built aside in the folder of the series, read back, and put under the name in one replace.

What goes in is told by one rule, not by cases: the container is a function of the plan. A track is taken while it is
synchronous with the video of the winner of the slot — aligned to a video of the same identity (`core.container.identity`:
the same source and row, any resolution). It is taken from the file voices placed, or from the container of the winner,
or from a container the winner displaced; nothing is downloaded or aligned again for a track that is there. The video's
own sound is the track of the voice-over of the reference. A slot whose container already is what the plan asks is left
alone. Nothing is encoded again (core/container.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, Sequence

from core import BusRequestError, container, storage
from core.container import AudioIn, Contents

from .repository import DownloadsRepository

PACK_PREFIX = ".st-pack."
NO_HANDLER = "нет обработчика"
CONTAINER_EXT = ".mkv"


class Packer(Protocol):
    async def build(self, output: str, video: str, video_identity: str, tracks: Sequence[AudioIn]) -> str | None: ...

    async def contents(self, path: str) -> Contents | None: ...


class FfmpegPacker:
    """The containers made by ffmpeg (core/container.py)."""

    async def build(self, output: str, video: str, video_identity: str, tracks: Sequence[AudioIn]) -> str | None:
        return await container.build(output, video, video_identity, tracks)

    async def contents(self, path: str) -> Contents | None:
        return await container.contents(path)


@dataclass
class Packed:
    packed: int = 0
    failed: int = 0
    unchanged: int = 0


Ask = Callable[..., Awaitable[Any]]


class Packing:
    def __init__(self, repo: DownloadsRepository, packer: Packer, ask: Ask, publish: Callable[[str, dict], None], log,
                 guard: Callable[[str], Any]) -> None:
        self._repo, self._packer, self._ask, self._publish, self._log, self._guard = repo, packer, ask, publish, log, guard

    async def pack(self, series: dict) -> Packed:
        base = series.get("save_path") or ""
        done = Packed()
        if not os.path.isabs(base):
            return done
        rows = await self._repo.slot_files(series["id"])
        references = {(each["season"], each["episode"], each.get("part")): each
                      for each in await self._ask("scan.output.get", {"series_id": series["id"]})}
        layout = await self._ask("composition.get", {"series_id": series["id"]})
        orders = {each["season"]: [voice["label"] for voice in each["voices"]] for each in layout.get("voice_orders", [])}
        try:
            tracks = (await self._ask("voices.tracks.get", {"series_id": series["id"]}))["tracks"]
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
            tracks = []  # no voices in this process: there are no voice-overs to pack
        slots: dict[tuple, list[dict]] = {}
        for row in rows:
            slots.setdefault((row["season"], row["episode_start"], row["part"]), []).append(row)
        for slot, here in sorted(slots.items(), key=lambda item: (item[0][0] or 0, item[0][1], item[0][2] or 0)):
            winner = next((row for row in here if row["plan_status"] == "in_plan_single" and row["status"] == "completed"
                           and not row["is_ignored_by_user"]), None)
            reference = references.get((slot[0], slot[1], slot[2]))
            if winner is None or reference is None or reference.get("row_key") != winner["row_key"]:
                continue
            losers = [row for row in here if row is not winner and row["plan_status"] == "replaced"]
            mine = [each for each in tracks if (each["season"], each["episode"], each["part"] or 0) == (slot[0], slot[1], slot[2] or 0)]
            outcome = await self._pack_slot(series, base, winner, losers, reference, orders.get(slot[0], []), mine)
            setattr(done, outcome, getattr(done, outcome) + 1)
        return done

    async def _pack_slot(self, series: dict, base: str, winner: dict, losers: list[dict], reference: dict,
                         order: list[str], tracks: list[dict]) -> str:
        identity = container.identity(winner["source_id"], winner["row_key"])
        own_label = reference.get("voice_group") or ""
        video = os.path.join(base, winner["final_filename"])
        held = await self._packer.contents(video)
        if held is None or not held.has_video:
            self._log.warning("серия %s: видео %s не читается — контейнер не собран", series["id"], winner["final_filename"])
            return "failed"
        own = AudioIn(own_label, video, held.own_track, identity, own=True)
        sources: dict[str, AudioIn] = {}
        for path, contents in [(video, held), *[(os.path.join(base, row["final_filename"]),
                                                  await self._packer.contents(os.path.join(base, row["final_filename"])))
                                                 for row in losers]]:
            for track in (contents.audio if contents else []):
                if not track.own and track.aligned_to == identity and track.label not in sources:
                    sources[track.label] = AudioIn(track.label, path, track.index, identity)
        placed, wanted = [], set()
        for track in tracks:
            if track["aligned_to"] != identity and not (not track["aligned_to"] and track["ref_uid"] == winner["unique_id"]):
                continue
            wanted.add(track["label"])
            if track["path"] and await storage.call(os.path.isfile, track["path"]):
                sources[track["label"]] = AudioIn(track["label"], track["path"], 0, identity)
                placed.append(track)
        present = {own_label} | {label for label in wanted if label in sources}
        # The order of the season; what it does not name yet (a voice-over new since the layout was read) goes last.
        labels = [label for label in order if label in present]
        labels += [own_label] * (own_label not in labels) + sorted(present - set(labels) - {own_label})
        desired = [own if label == own_label else sources[label] for label in labels]
        target_name = os.path.splitext(winner["final_filename"])[0] + CONTAINER_EXT
        target = os.path.join(base, target_name)
        left = [row for row in losers if row["final_filename"] and row["final_filename"] != target_name]
        if (winner["final_filename"] == target_name and held.video == identity and held.labels == labels
                and not placed and not left and not [row for row in losers if row["final_filename"] == target_name]):
            return "unchanged"
        staging = os.path.join(os.path.dirname(target), f"{PACK_PREFIX}{winner['unique_id']}{CONTAINER_EXT}")
        async with self._guard(target):
            problem = await self._packer.build(staging, video, identity, desired)
            if problem is not None:
                await storage.call(_remove_quietly, staging, owns=staging)
                self._log.warning("серия %s: контейнер %s не собран: %s", series["id"], target_name, problem)
                return "failed"
            await storage.call(os.replace, staging, target, owns=target)
            await self._repo.take_over_name(winner["unique_id"], target_name, [row["unique_id"] for row in losers])
        if tracks:
            await self._ask("voices.tracks.packed", {"series_id": series["id"], "container": target,
                                                     "dub_ids": [track["dub_id"] for track in tracks
                                                                 if track["label"] in labels and track["label"] != own_label]})
        for leftover in {os.path.join(base, winner["final_filename"]), *[os.path.join(base, row["final_filename"])
                                                                          for row in losers if row["final_filename"]],
                         *[track["path"] for track in placed]} - {target}:
            await storage.call(_remove_quietly, leftover, owns=leftover)
        if winner["final_filename"] != target_name:
            self._publish("downloads.item.renamed", {"series_id": series["id"], "unique_id": winner["unique_id"],
                                                     "filename": target_name})
        self._log.info("серия %s: контейнер %s собран — %s", series["id"], target_name, ", ".join(labels))
        return "packed"


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
