"""The container of every episode of a video series, made at the end of a sync (ЭТАП_11, И-61).

The file of an episode is one MKV: the video of the reference, its own sound, and the voice-overs in the order of the
season. Packing is the last step of a run of a sync (scan asks `downloads.pack` when every other work of the run has
ended), so the disk changes in a sync alone (И-59) and the old container lives until the new one is whole (ЭТАП_11,
answer 3): the new one is built aside in the folder of the series, read back, and put under the name in one replace.

What goes in is told by one rule, not by cases: the container is a function of the plan. A track is known by the key of
its voice-over (`ST_DUB`, voices' `voice_key`), never by its name: a renamed group keeps its track and the track takes the
new name. A track is taken while it is synchronous with the video of the winner of the slot — aligned to a video of the
same identity (`core.container.identity`: the same source and row, any resolution) — from the file voices placed, from
the container of the winner, or from a container the winner displaced; nothing is downloaded or aligned again for a
track that is there. A track whose voice-over the plan still holds at another step keeps its place; only a voice-over
that left the plan leaves the container. A track of a container built before the keys (no `ST_DUB`) is known by its name
once and gets its key; one that no voice-over claims is kept as it is. The video's own sound is the track of the
voice-over of the reference. A slot whose container already is what the plan asks is left alone. Nothing is encoded
again (core/container.py).

A container is put only where the series may put it: a file under its name that is not the winner's or a file it
displaced (another series' in a shared folder, a row of this series, a file nobody holds) is not replaced, and the slot
fails with the reason in the log. Every publication is written to `pack_publications` (owned by this module) before its
build — the name the winner takes, the rows it displaces, the voice-overs to be recorded as packed, the files to remove —
and is finished from that record at the next packing of the series if a stop cuts it short: no file is left without an
owner and no name is held by two rows.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Protocol, Sequence

from core import BusRequestError, container, storage
from core.container import AudioIn, Contents

from .repository import DownloadsRepository

PACK_PREFIX = ".st-pack."
NO_HANDLER = "нет обработчика"
CONTAINER_EXT = ".mkv"
PLACED = "placed"


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
    # Slots with a file whose video the plan wants is not downloaded yet: the file there stays as it is.
    waiting: int = 0


Ask = Callable[..., Awaitable[Any]]


class Packing:
    def __init__(self, repo: DownloadsRepository, packer: Packer, ask: Ask, publish: Callable[[str, dict], None], log,
                 library: Any) -> None:
        self._repo, self._packer, self._request, self._publish, self._log = repo, packer, ask, publish, log
        self._library = library

    async def pack(self, series: dict) -> Packed:
        base = series.get("save_path") or ""
        done = Packed()
        if not os.path.isabs(base):
            return done
        await self._finish_interrupted(series)
        rows = await self._repo.slot_files(series["id"])
        references = {(each["season"], each["episode"], each.get("part")): each
                      for each in await self._request("scan.output.get", {"series_id": series["id"]})}
        layout = await self._request("composition.get", {"series_id": series["id"]})
        orders = {each["season"]: [voice["label"] for voice in each["voices"]] for each in layout.get("voice_orders", [])}
        tracks = await self._tracks(series["id"])
        slots: dict[tuple, list[dict]] = {}
        for row in rows:
            slots.setdefault((row["season"], row["episode_start"], row["part"]), []).append(row)
        for slot, here in sorted(slots.items(), key=lambda item: (item[0][0] or 0, item[0][1], item[0][2] or 0)):
            winner = next((row for row in here if row["plan_status"] == "in_plan_single" and row["status"] == "completed"
                           and not row["is_ignored_by_user"]), None)
            reference = references.get((slot[0], slot[1], slot[2]))
            if winner is None or reference is None or reference.get("row_key") != winner["row_key"]:
                if reference is not None and reference.get("row_key"):
                    done.waiting += 1
                continue
            losers = [row for row in here if row is not winner and row["plan_status"] == "replaced"]
            mine = [each for each in tracks if (each["season"], each["episode"], each["part"] or 0) == (slot[0], slot[1], slot[2] or 0)]
            outcome = await self._pack_slot(series, base, winner, losers, reference, orders.get(slot[0], []), mine)
            setattr(done, outcome, getattr(done, outcome) + 1)
        return done

    async def _tracks(self, series_id: int) -> list[dict]:
        try:
            return (await self._request("voices.tracks.get", {"series_id": series_id}))["tracks"]
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
            return []  # no voices in this process: there are no voice-overs to pack

    async def _pack_slot(self, series: dict, base: str, winner: dict, losers: list[dict], reference: dict,
                         order: list[str], tracks: list[dict]) -> str:
        identity = container.identity(winner["source_id"], winner["row_key"])
        own_label = reference.get("voice_group") or ""
        video = os.path.join(base, winner["final_filename"])
        held = await self._packer.contents(video)
        if held is None or not held.has_video:
            self._log.warning("серия %s: видео %s не читается — контейнер не собран", series["id"], winner["final_filename"])
            return "failed"
        loser_files = [row for row in losers if row["final_filename"]]
        keyed, legacy = await self._held_tracks(base, video, held, loser_files, identity)
        synchronous = [row for row in tracks if row["status"] == PLACED and (
            row["aligned_to"] == identity or (not row["aligned_to"] and row["ref_uid"] == winner["unique_id"]))]
        dubs: dict[str, AudioIn] = {}
        placed: list[dict] = []
        for row in synchronous:
            key, label = row["key"], row["label"]
            unkeyed = legacy.pop(label, None)  # an old track of the voice-over is its own, whichever copy is taken
            if row["path"] and await storage.call(os.path.isfile, row["path"]):
                dubs[key] = AudioIn(label, row["path"], 0, identity, key=key)
                placed.append(row)
            elif key in keyed:
                dubs[key] = replace(keyed[key], label=label)
            elif unkeyed is not None:
                dubs[key] = replace(unkeyed, label=label, key=key)
        # A voice-over the plan holds at another step keeps its track synchronous with this video: nothing is lost
        # while it is being worked on.
        for row in tracks:
            key, label = row["key"], row["label"]
            if key in dubs:
                continue
            if key in keyed:
                dubs[key] = replace(keyed[key], label=label)
            elif label in legacy:
                dubs[key] = replace(legacy.pop(label), label=label, key=key)
        desired = self._ordered(AudioIn(own_label, video, held.own_track, identity, own=True), dubs, order,
                                list(legacy.values()))
        signature = container.signature_of(desired)
        inside = [row for row in synchronous if row["key"] in dubs]
        target_name = os.path.splitext(winner["final_filename"])[0] + CONTAINER_EXT
        target = os.path.join(base, target_name)
        if (winner["final_filename"] == target_name and held.video == identity and held.signature == signature
                and not placed and not loser_files and all(each.path == video for each in desired)):
            # The record of voices follows the container, which is the truth of what it holds.
            await self._tell_packed(series, target, identity, [
                row["dub_id"] for row in inside if row["aligned_to"] != identity or row["ref_path"] != target])
            return "unchanged"
        record = {"series_id": series["id"], "unique_id": winner["unique_id"], "name": target_name,
                  "staging": os.path.join(os.path.dirname(target), f"{PACK_PREFIX}{winner['unique_id']}{CONTAINER_EXT}"),
                  "identity": identity, "signature": [list(each) for each in signature],
                  "losers": [row["unique_id"] for row in losers],
                  "leftovers": sorted({video, *[os.path.join(base, row["final_filename"]) for row in loser_files],
                                       *[row["path"] for row in placed]} - {target}),
                  "dub_ids": [row["dub_id"] for row in inside]}
        async with self._library.guard(target):
            own = {video, *[os.path.join(base, row["final_filename"]) for row in loser_files]}
            if target not in own and await storage.call(os.path.exists, target):
                holder = await self._holder(series, target_name, except_uids={winner["unique_id"], *record["losers"]})
                self._log.warning("серия %s: контейнер %s не собран: под этим именем лежит %s — файл не заменяется",
                                  series["id"], target_name, holder or "файл, которым не владеет ни одна строка")
                return "failed"
            record["id"] = await self._repo.publication_begin(record)
            problem = await self._packer.build(record["staging"], video, identity, desired)
            if problem is not None:
                await storage.call(_remove_quietly, record["staging"], owns=record["staging"])
                await self._repo.publication_end(record["id"])
                self._log.warning("серия %s: контейнер %s не собран: %s", series["id"], target_name, problem)
                return "failed"
            await storage.call(os.replace, record["staging"], target, owns=target)
            await self._repo.publication_placed(record["id"])
            await self._finish(series, record, renamed=winner["final_filename"] != target_name)
        self._log.info("серия %s: контейнер %s собран — %s", series["id"], target_name,
                       ", ".join(each.label for each in desired))
        return "packed"

    async def _held_tracks(self, base: str, video: str, held: Contents, loser_files: list[dict], identity: str
                           ) -> tuple[dict[str, AudioIn], dict[str, AudioIn]]:
        """The voice-over tracks synchronous with the video that the containers of the slot hold: by key, and those of a
        container made before the keys by name. The winner's container is looked at first."""
        keyed: dict[str, AudioIn] = {}
        legacy: dict[str, AudioIn] = {}
        found = [(video, held)]
        for row in loser_files:
            path = os.path.join(base, row["final_filename"])
            found.append((path, await self._packer.contents(path)))
        for path, contents in found:
            for track in (contents.audio if contents else []):
                if track.own or track.aligned_to != identity:
                    continue
                source = AudioIn(track.label, path, track.index, identity, key=track.key)
                if track.key:
                    keyed.setdefault(track.key, source)
                else:
                    legacy.setdefault(track.label, source)
        return keyed, legacy

    @staticmethod
    def _ordered(own: AudioIn, dubs: dict[str, AudioIn], order: list[str], unclaimed: list[AudioIn]) -> list[AudioIn]:
        """The tracks in the order of the season (by the names of the groups, as the user set them); what the order does
        not name yet (a voice-over new since the layout was read) goes after it, and a track no voice-over claims last."""
        result: list[AudioIn] = []
        left = dict(sorted(dubs.items(), key=lambda item: (item[1].label, item[0])))
        for label in order:
            if label == own.label and own not in result:
                result.append(own)
            for key in [key for key, track in left.items() if track.label == label]:
                result.append(left.pop(key))
        if own not in result:
            result.append(own)
        return result + list(left.values()) + unclaimed

    async def _holder(self, series: dict, name: str, *, except_uids: set[str]) -> str | None:
        """Who holds the file the name means: a row of another series in a shared folder, or another row of this one."""
        foreign = await self._library.foreign_holder(series, name)
        if foreign:
            return f"файл сериала {foreign['series_id']}"
        mine = [row for row in await self._repo.holders_of_file(series["id"], name, except_uid="")
                if row["unique_id"] not in except_uids]
        return f"файл строки {mine[0]['unique_id']}" if mine else None

    async def _finish(self, series: dict, record: dict, *, renamed: bool) -> None:
        """The publication after its replace, from its record: every step may be done again."""
        base = series["save_path"]
        target = os.path.join(base, record["name"])
        await self._repo.take_over_name(record["unique_id"], record["name"], record["losers"])
        await self._tell_packed(series, target, record["identity"], record["dub_ids"])
        for leftover in record["leftovers"]:
            name = os.path.relpath(leftover, base).replace(os.sep, "/")
            if not name.startswith("..") and await self._holder(series, name, except_uids=set()):
                continue  # someone holds the file under that name now: it is not this publication's to remove
            await storage.call(_remove_quietly, leftover, owns=leftover)
        await self._repo.publication_end(record["id"])
        if renamed:
            self._publish("downloads.item.renamed", {"series_id": series["id"], "unique_id": record["unique_id"],
                                                     "filename": record["name"]})

    async def _finish_interrupted(self, series: dict) -> None:
        """A publication a stop cut short. Before its replace nothing was changed: its build goes and the old files and
        names stand. After it (the container under the name is the one the record describes) it is finished."""
        for record in await self._repo.publications(series["id"]):
            target = os.path.join(series["save_path"], record["name"])
            async with self._library.guard(target):
                if record["state"] != PLACED:
                    await storage.call(_remove_quietly, record["staging"], owns=record["staging"])
                    made = await self._packer.contents(target)
                    if made is None or made.video != record["identity"] or made.signature != [
                            tuple(each) for each in record["signature"]]:
                        await self._repo.publication_end(record["id"])
                        continue
                self._log.info("серия %s: публикация контейнера %s, прерванная остановкой, доведена до конца",
                               series["id"], record["name"])
                await self._finish(series, record, renamed=True)

    async def _tell_packed(self, series: dict, target: str, identity: str, dub_ids: list[int]) -> None:
        """The tracks are in the container, aligned to its video: voices records both, so that a later change of the
        resolution of the same video keeps them (ЭТАП_11, п. 5)."""
        if not dub_ids:
            return
        try:
            await self._request("voices.tracks.packed", {"series_id": series["id"], "container": target,
                                                     "aligned_to": identity, "dub_ids": dub_ids})
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
