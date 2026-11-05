"""The container of every episode of a video series, made in a sync as soon as the episode is ready (ЭТАП_11, И-61).

The file of an episode is one MKV: the video of the reference, its own sound, and the voice-overs in the order of the
season. Packing is a step of a run of a sync, and each episode is a process of its own: scan asks `downloads.pack` for the
episodes no work of the run is about, at once, then for each episode as soon as the last work of it ends, and when the run
closes for what it still owes (the slots that failed, or the whole series when the run packed nothing). A slot that is what
the plan asks is left alone. So the disk changes in a sync alone
(И-59) and the old container lives until the new one is whole (ЭТАП_11,
answer 3): the new one is built aside in the folder of the series, read back, and put under the name in one replace.

What goes in is told by one rule, not by cases: the container is a function of the plan. A track is known by the key of
its voice-over (`ST_DUB`, voices' `voice_key`), never by its name: a renamed group keeps its track and the track takes the
new name. A track is taken while it is synchronous with the video of the winner of the slot — aligned to a video of the
same identity (`core.container.identity`: the same source and row, any resolution) — from the file voices placed, from
the container of the winner, or from a container the winner displaced; nothing is downloaded or aligned again for a
track that is there. A track whose voice-over the plan still holds at another step keeps its place; only a voice-over
that left the plan leaves the container. A track of a container built before the keys (no `ST_DUB`) is known by its name
once — the key of a voice-over (its first name) or its name now — and gets its key; one that no voice-over claims is kept as
it is, and a name that answers to two voice-overs (or two names to one) leaves the slot unbuilt, said in the log. The
video's own sound is the track of the voice-over of the reference. A slot whose container already is what the plan asks is left alone. Nothing is encoded
again (core/container.py).

A container is put only where the series may put it: a file under its name that is not the winner's or a file it
displaced (another series' in a shared folder, a row of this series, a file nobody holds) is not replaced, and the slot
fails with the reason in the log. Every publication is written to `pack_publications` (owned by this module) before its
build — its number, the name the winner takes, the rows it displaces, the voice-overs to be recorded as packed, the files
to remove, all names inside the folder of the series — and the number goes into the container (`ST_PUBLICATION`). A
publication a stop or a failure cut short is finished before anything else acts on the files or names of the series
(`settle`, called by the module at the start of every look at the series), and only when the file under its name is the
container it built: one that is not was never put there, so nothing is removed and no name changes. A record whose
container was put there and is not there now is a conflict: it is closed with the reason in the log, nothing is done by it.

The work is seen as a queue (`queue`, published by the module as `downloads.pack.queue.changed` and shown in the window of
agents): a series asked to be packed waits in it, then each slot of it is queued, built, and ends packed, left as it was,
waiting for its video or failed; a publication a stop cut short is there until it is finished. What ended well leaves the
queue with the packing of its series; a failed slot stays until the next packing of the series.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Protocol, Sequence

from core import NO_HANDLER, BusRequestError, container, storage
from core.container import AudioIn, Contents

from .repository import DownloadsRepository

PACK_PREFIX = ".st-pack."
CONTAINER_EXT = ".mkv"
PLACED = "placed"


class Packer(Protocol):
    async def build(self, output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *,
                    publication: str = "") -> str | None: ...

    async def contents(self, path: str) -> Contents | None: ...


class FfmpegPacker:
    """The containers made by ffmpeg (core/container.py)."""

    async def build(self, output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *,
                    publication: str = "") -> str | None:
        return await container.build(output, video, video_identity, tracks, publication=publication)

    async def contents(self, path: str) -> Contents | None:
        return await container.contents(path)


@dataclass
class Packed:
    packed: int = 0
    failed: int = 0
    unchanged: int = 0
    # Slots with a file whose video the plan wants is not downloaded yet: the file there stays as it is.
    waiting: int = 0
    # The slots that failed, [season, episode, part or 0]: the run asks for them again when it closes.
    failed_slots: list[list] = field(default_factory=list)


Ask = Callable[..., Awaitable[Any]]


class Packing:
    def __init__(self, repo: DownloadsRepository, packer: Packer, ask: Ask, publish: Callable[[str, dict], None], log,
                 library: Any, on_queue: Callable[[], None] = lambda: None) -> None:
        self._repo, self._packer, self._request, self._publish, self._log = repo, packer, ask, publish, log
        self._library = library
        self._on_queue = on_queue
        # What the window of agents shows of the packing, by (series, season, episode, part, key); None is the series itself.
        self._queue: dict[tuple, dict] = {}
        # The names of the tracks of the container a slot is built of, in order, for the window.
        self._built: dict[tuple, list[str]] = {}

    # --- the queue ------------------------------------------------------------------------------------------------

    def queue(self) -> list[dict]:
        """The tasks of packing as the window shows them, in the order of the series and their episodes."""
        return sorted(self._queue.values(), key=lambda each: (each["series_id"], each["season"] is not None,
                                                              each["season"] or 0, each["episode"] or 0, each["part"] or 0))

    def waits(self, series: dict) -> None:
        """A packing of the series is asked for and waits for its turn."""
        self._set(series, None, status="queued")

    def load_interrupted(self, records: list[dict], names: dict[int, str]) -> None:
        """The publications a stop cut short, as the journal keeps them: they are finished at the next look at the series."""
        for record in records:
            self._set({"id": record["series_id"], "name": names.get(record["series_id"], "")},
                      (None, None, None), status="interrupted", name=record["name"], key=record["publication"] or record["id"])

    def _set(self, series: dict, slot: tuple | None, *, status: str, name: str = "", tracks: list[str] | None = None,
             key: Any = None) -> None:
        season, episode, part = slot if slot is not None else (None, None, None)
        self._queue[(series["id"], season, episode, part, key)] = {
            "id": f"pack:{series['id']}:{season}:{episode}:{part}:{key or ''}", "series_id": series["id"],
            "series_name": series.get("name", ""), "season": season, "episode": episode, "part": part, "name": name,
            "status": status, "tracks": tracks or []}
        self._on_queue()

    def _drop(self, series_id: int, keep_failed: bool, only: str | None = None,
              slots: Callable[[tuple], bool] | None = None) -> None:
        """The tasks of the series leave the queue: all of them, or all but the failed and cut short, or those of `only`;
        of the slots `slots` accepts, when given (a task of the whole series, with no episode, is of every packing)."""
        for key in [key for key, task in self._queue.items() if key[0] == series_id
                    and (only is None or task["status"] == only)
                    and (slots is None or key[2] is None or slots(key[1:4]))
                    and not (keep_failed and task["status"] in ("failed", "interrupted"))]:
            del self._queue[key]
        self._on_queue()

    async def pack(self, series: dict, *, only: set[tuple] | None = None, skip: set[tuple] = frozenset()) -> Packed:
        """The episodes of `only` (all when None) but those of `skip` (slots (season, episode, part or 0))."""
        def asked(slot: tuple) -> bool:
            key = (slot[0], slot[1], slot[2] or 0)
            return (only is None or key in only) and key not in skip

        base = series.get("save_path") or ""
        done = Packed()
        if not os.path.isabs(base):
            self._drop(series["id"], keep_failed=False, slots=asked)
            return done
        try:
            await self._pack(series, base, done, asked)
        finally:
            self._drop(series["id"], keep_failed=True, slots=asked)
        return done

    async def _pack(self, series: dict, base: str, done: Packed, asked: Callable[[tuple], bool]) -> None:
        self._drop(series["id"], keep_failed=False, slots=asked)  # the failures of these slots are looked at again now
        await self.settle(series)
        rows = await self._repo.slot_files(series["id"])
        references = {(each["season"], each["episode"], each.get("part")): each
                      for each in await self._request("scan.output.get", {"series_id": series["id"]})}
        layout = await self._request("composition.get", {"series_id": series["id"]})
        orders = {each["season"]: [voice["label"] for voice in each["voices"]] for each in layout.get("voice_orders", [])}
        tracks = await self._tracks(series["id"])
        slots: dict[tuple, list[dict]] = {}
        for row in rows:
            slots.setdefault((row["season"], row["episode_start"], row["part"]), []).append(row)
        work: list[tuple] = []
        # Every slot of the plan is looked at, also one that has no file yet: its video is not downloaded, it waits.
        for slot in sorted({*slots, *references}, key=lambda each: (each[0] or 0, each[1], each[2] or 0)):
            if not asked(slot):
                continue
            here = slots.get(slot, [])
            winner = next((row for row in here if row["plan_status"] == "in_plan_single" and row["status"] == "completed"
                           and not row["is_ignored_by_user"]), None)
            reference = references.get(slot)
            if winner is None or reference is None or reference.get("row_key") != winner["row_key"]:
                if reference is not None and reference.get("row_key"):
                    done.waiting += 1
                    self._set(series, slot, status="waiting")
                continue
            self._set(series, slot, status="queued", name=winner["final_filename"])
            work.append((slot, here, winner, reference))
        for slot, here, winner, reference in work:
            losers = [row for row in here if row is not winner and row["plan_status"] == "replaced"]
            mine = [each for each in tracks if (each["season"], each["episode"], each["part"] or 0) == (slot[0], slot[1], slot[2] or 0)]
            self._set(series, slot, status="building", name=winner["final_filename"])
            try:
                outcome = await self._pack_slot(series, base, winner, losers, reference, orders.get(slot[0], []), mine)
            except BaseException:
                self._set(series, slot, status="interrupted", name=winner["final_filename"])  # its record finishes it
                raise
            name = os.path.splitext(winner["final_filename"])[0] + CONTAINER_EXT
            self._set(series, slot, status=outcome, name=name if outcome != "failed" else winner["final_filename"],
                      tracks=self._built.pop(slot, []))
            setattr(done, outcome, getattr(done, outcome) + 1)
            if outcome == "failed":
                done.failed_slots.append([slot[0], slot[1], slot[2] or 0])

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
        claimed, legacy, ambiguous = _claim_legacy(tracks, legacy)
        if ambiguous:
            self._log.warning("серия %s: контейнер %s не собран: старые дорожки без ключа не сопоставлены однозначно — %s",
                              series["id"], winner["final_filename"], "; ".join(ambiguous))
            return "failed"
        synchronous = [row for row in tracks if row["status"] == PLACED and (
            row["aligned_to"] == identity or (not row["aligned_to"] and row["ref_uid"] == winner["unique_id"]))]
        dubs: dict[str, AudioIn] = {}
        placed: list[dict] = []
        for row in synchronous:
            key, label = row["key"], row["label"]
            unkeyed = claimed.pop(key, None)  # an old track of the voice-over is its own, whichever copy is taken
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
            elif key in claimed:
                dubs[key] = replace(claimed.pop(key), label=label, key=key)
        desired = self._ordered(AudioIn(own_label, video, held.own_track, identity, own=True), dubs, order,
                                list(legacy.values()))
        self._built[(winner["season"], winner["episode_start"], winner["part"])] = [each.label for each in desired]
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
        publication = uuid.uuid4().hex
        leftovers = {video, *[os.path.join(base, row["final_filename"]) for row in loser_files],
                     *[row["path"] for row in placed]} - {target}
        record = {"series_id": series["id"], "unique_id": winner["unique_id"], "publication": publication,
                  "name": target_name,
                  "staging": os.path.join(os.path.dirname(target_name), f"{PACK_PREFIX}{publication}{CONTAINER_EXT}"),
                  "identity": identity, "signature": [list(each) for each in signature],
                  "losers": [row["unique_id"] for row in losers],
                  "leftovers": sorted(_inside(base, path) for path in leftovers),
                  "dub_ids": [row["dub_id"] for row in inside]}
        staging = os.path.join(base, record["staging"])
        async with self._library.guard(target):
            own = {video, *[os.path.join(base, row["final_filename"]) for row in loser_files]}
            if target not in own and await storage.call(os.path.exists, target):
                holder = await self._holder(series, target_name, except_uids={winner["unique_id"], *record["losers"]})
                self._log.warning("серия %s: контейнер %s не собран: под этим именем лежит %s — файл не заменяется",
                                  series["id"], target_name, holder or "файл, которым не владеет ни одна строка")
                return "failed"
            record["id"] = await self._repo.publication_begin(record)
            problem = await self._packer.build(staging, video, identity, desired, publication=publication)
            if problem is not None:
                await storage.call(_remove_quietly, staging, owns=staging)
                await self._repo.publication_end(record["id"])
                self._log.warning("серия %s: контейнер %s не собран: %s", series["id"], target_name, problem)
                return "failed"
            await storage.call(os.replace, staging, target, owns=target)
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
            path = os.path.join(base, leftover)
            name = os.path.relpath(path, base).replace(os.sep, "/")
            if not name.startswith("..") and await self._holder(series, name, except_uids=set()):
                continue  # someone holds the file under that name now: it is not this publication's to remove
            await storage.call(_remove_quietly, path, owns=path)
        await self._repo.publication_end(record["id"])
        if renamed:
            self._publish("downloads.item.renamed", {"series_id": series["id"], "unique_id": record["unique_id"],
                                                     "filename": record["name"]})

    async def settle(self, series: dict) -> None:
        """Finish the publications a stop or a failure cut short, before anything else acts on the files or the names of
        the series. A publication is the one under its name only when the container there carries its number: before its
        replace nothing of it was put there (its build goes, the old files and names stand); after it, it is finished. A
        record whose container was put there and is not there now (moved, renamed or replaced from outside) is closed with
        the reason in the log, and nothing is done by it."""
        base = series.get("save_path") or ""
        if not os.path.isabs(base):
            return
        self._drop(series["id"], keep_failed=False, only="interrupted")  # finished now, or a conflict below
        for record in await self._repo.publications(series["id"]):
            key = (series["id"], None, None, None, record["publication"] or record["id"])
            if self._queue.pop(key, None) is not None:
                self._on_queue()
            target = os.path.join(base, record["name"])
            async with self._library.guard(target):
                made = await self._packer.contents(target)
                ours = bool(record["publication"]) and made is not None and made.publication == record["publication"]
                if record["state"] != PLACED:
                    staging = os.path.join(base, record["staging"])
                    await storage.call(_remove_quietly, staging, owns=staging)
                    if not ours:
                        self._log.info("серия %s: публикация контейнера %s прервана до замены — отменена, файлы и имена "
                                       "прежние", series["id"], record["name"])
                        await self._repo.publication_end(record["id"])
                        continue
                elif not ours:
                    # The record does not know the files any more: it is closed, and nothing is done by it. What it would
                    # have removed stays (no row holds it, the check of the disk sees it); the names stand as the rows say.
                    self._set(series, None, status="failed", name=record["name"], key=key[-1])
                    self._log.error("серия %s: публикация контейнера %s не доведена: под этим именем нет контейнера, который "
                                    "она поставила (номер %s) — запись журнала закрыта, файлы и имена не тронуты",
                                    series["id"], record["name"], record["publication"] or "не записан")
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


def _claim_legacy(tracks: list[dict], legacy: dict[str, AudioIn]
                  ) -> tuple[dict[str, AudioIn], dict[str, AudioIn], list[str]]:
    """The old tracks without a key (a container built before the keys), each given to the voice-over it is: the name of
    such a track is the name its group had then, which is the key of the voice-over (a key is the first name of a group
    and a rename does not change it) or its name now. (claimed by key, unclaimed by name, problems): a name two
    voice-overs answer to, or a voice-over two names answer to, is not decided here — the slot is not built."""
    answers: dict[str, set[str]] = {}
    for row in tracks:
        for name in {row["key"], row["label"]}:
            if name in legacy:
                answers.setdefault(name, set()).add(row["key"])
    problems = [f"дорожке «{name}» отвечают озвучки {', '.join(sorted(keys))}" for name, keys in answers.items() if len(keys) > 1]
    names_of: dict[str, list[str]] = {}
    for name, keys in answers.items():
        if len(keys) == 1:
            names_of.setdefault(next(iter(keys)), []).append(name)
    problems += [f"озвучке {key} отвечают дорожки {', '.join(sorted(names))}" for key, names in names_of.items()
                 if len(names) > 1]
    if problems:
        return {}, legacy, problems
    claimed = {key: legacy[names[0]] for key, names in names_of.items()}
    return claimed, {name: track for name, track in legacy.items() if name not in answers}, []


def _inside(base: str, path: str) -> str:
    """A path of the series as a name inside its folder (moving the folder keeps it true); a path outside it as it is."""
    name = os.path.relpath(path, base)
    return path if name.startswith("..") else name


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
