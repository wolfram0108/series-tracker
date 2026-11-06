"""Cuts out of compilations (stage 12): the episode a compilation holds, cut out of it by the alignment service on the
timeline of the single video the episode's reference names (REF 2), becomes the video of the episode.

A cut is a row of `voice_cuts`, one per slot, owned by this module like the voice-overs:

  planned → downloading → ready → submitting → aligning → done → offered
                                                         ↘ failed

- planned: the plan wants the slot cut out of a compilation (`Reference.compilation`) and the container of the slot is not
  of it yet (`downloads.episodes.get`: `cut`, `pending_cut`); admitted by a sync like a voice-over.
- downloading / ready: the compilation is a file of `voice_files` of quality `best` (no cap), shared by the cuts of all
  its episodes and kept while one of them reads it.
- submitting / aligning: the cuts of one file whose REF 2 is on disk go to the service as one batch (`conform.reverse`):
  the copy of REF 2 of each lies in a folder named `<label> #<key>`, the label of its job, so that a lost answer is
  recovered by the key (`conform.job.find`), as for a job of voice-overs.
- done: the service wrote the video; it is offered to `downloads` (`downloads.video.offer`), which holds it for the next
  build of the container. `offered` is the end: the file is the library's now. A cut whose video is not taken goes back to
  the journal and is planned again by the next sync if the plan still wants it.
- failed: the file could not be downloaded, or the service did not cut the episode; the episode keeps its single video,
  `voices.cuts.retry` (the user's word) plans it again.

A slot whose container holds a cut the plan does not want any more (the episode was taken out of its compilation) gets a
row of the same kind with an empty key: its single video is downloaded again, uncapped (planned → downloading → done →
offered), and offered as the video of the next container, which keeps its tracks.

The voice-overs of a slot with a cut that has not ended wait (they are aligned to the video of the cut); the series has
work while a cut is under way. What a cut owns — its folder, its job, its unanswered submission — goes to the cleanup
journal in the transaction that lets it go (repository). The job of a cut that was cut leaves the service's history when
the setting `conform.clear_finished` asks it to; the job of a failed cut stays (Р-75).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from core import BusRequestError, container, storage
from core.contracts import AlignmentJob, JobStatus, Reference, ReverseReply, SubmitOutcome
from core.reasons import describe, parse, reason

from . import cut_lifecycle as T
from .cut_lifecycle import LIVE
from .planning import _REFERENCED
from .repository import OUT_DIR

Slot = tuple[int, int, int]
CUTS_DIR = "_cuts"
CUT_KEY = "cut:"
# The key of the download of the single video of a slot taken back out of its compilation.
SINGLE_KEY = "cutvideo:"


def cut_tag(season: int, episode: int, part: int) -> str:
    return f"s{season:02d}e{episode:02d}" + (f"p{part}" if part else "")


class Cuts:
    """The cuts of every series; `module` is the voices module, whose repository, journal, recovery and bus it uses."""

    def __init__(self, module) -> None:
        self._m = module

    @property
    def _repo(self):
        return self._m.repo

    # --- plan ----------------------------------------------------------------------------------------------------

    async def own_voices(self, series_id: int, references: Iterable[Reference]) -> dict[Slot, str]:
        """The voice-over the video of each slot carries when it is cut out of a compilation: the compilation's, unless
        the cut failed (then the episode keeps its single video and its voice)."""
        failed = {(each["season"], each["episode"], each["part"]) for each in await self._repo.cuts_of(series_id)
                  if each["status"] == "failed"}
        found = {}
        for ref in references:
            slot = (ref.season, ref.episode, ref.part or 0)
            if ref.compilation is not None and ref.mark in _REFERENCED and slot not in failed:
                found[slot] = ref.compilation.voice_group
        return found

    async def plan(self, series_id: int, references: Iterable[Reference], rows, *, admit: bool, settle: bool) -> None:
        """Record a cut for every slot whose video the plan wants out of a compilation and does not have yet; with `settle`
        the cuts the plan does not want any more go. A slot whose REF 2 has no row of the plan yet waits for the next pass."""
        items = await self._items(series_id)
        links = {(row.source_id, row.row_key): row.link for row in rows}
        wanted: dict[Slot, tuple[dict, str]] = {}
        for ref in references:
            if ref.mark not in _REFERENCED or ref.part is not None:
                continue
            slot = (ref.season, ref.episode, 0)
            item = items.get(slot)
            if item is None or (item["source_id"], item["row_key"]) != (ref.source_id, ref.row_key):
                continue
            if ref.compilation is None:
                if item.get("cut") and not item.get("pending_video") and links.get((ref.source_id, ref.row_key)):
                    # The container holds a cut the plan does not want: the single video comes back, the tracks stay.
                    wanted[slot] = ({"key": "", "url": links[(ref.source_id, ref.row_key)], "source_id": ref.source_id,
                                     "row_key": ref.row_key, "player": ref.player, "voice_group": ref.voice_group or "",
                                     "height": ref.height}, item["unique_id"])
                continue
            key = ref.compilation.key
            if (item.get("cut") or {}).get("key") == key or (item.get("pending_cut") or {}).get("key") == key:
                continue  # the container holds that video, or the next one will
            url = links.get((ref.compilation.source_id, ref.compilation.row_key))
            if not url:
                continue
            wanted[slot] = ({**ref.compilation.model_dump(mode="json"), "url": url}, item["unique_id"])
        for slot, (cut, uid) in wanted.items():
            if await self._repo.plan_cut(series_id, *slot, cut, uid, admit):
                await self._announce_slot(series_id, slot)
        if admit:
            await self._repo.admit_cuts(series_id)
        if settle:
            for gone in await self._repo.drop_cuts(series_id, set(wanted)):
                self._m.log.info("сериал %d, s%02de%02d: разбор компиляции снят — план его больше не ждёт", series_id,
                                 gone["season"], gone["episode"])
                await self._announce({**gone, "status": "removed"})

    async def _items(self, series_id: int) -> dict[Slot, dict]:
        """The rows of the plan that are the reference of their slot, with what the library holds of their video."""
        reply = await self._m.request("downloads.episodes.get", {"series_id": series_id})
        found = {}
        for item in reply["items"]:
            if item.get("superseded_by"):
                continue
            found[(item["season"], item["episode_start"], item["part"] or 0)] = {
                **item, "cut": json.loads(item["cut"]) if item.get("cut") else None,
                "pending_cut": json.loads(item["pending_cut"]) if item.get("pending_cut") else None}
        return found

    # --- the file of the compilation ------------------------------------------------------------------------------

    async def start(self, root: str) -> None:
        """Every admitted planned cut takes the file of its compilation; the first sends the download."""
        from .module import FILE_KEY, file_path
        for cut in await self._repo.cuts_of():
            if cut["status"] != "planned" or not cut["admitted"]:
                continue
            data = cut["cut"]
            if not data["key"]:
                await self._fetch_single(cut, root)
                continue
            candidate = {"player": data["player"], "url": data["url"], "studio": data.get("voice_group") or "",
                         "file": {"source_id": data["source_id"], "row_key": data["row_key"]}}
            file = await self._repo.cut_join_file(cut["id"], cut["series_id"], data["source_id"], data["row_key"],
                                                  data["url"], lambda file_id: file_path(root, cut["series_id"], file_id,
                                                                                         candidate))
            if file is None:
                continue
            await self._announce(await self._repo.cut(cut["id"]))
            if file["claimed"]:
                # No cap: the episodes are cut out of the compilation in the quality it has.
                self._m.send_command("downloads.fetch", {"key": f"{FILE_KEY}{file['id']}", "url": data["url"],
                                                         "path": file["temp_path"], "series_id": cut["series_id"]})
            elif file["status"] == "ready":
                self._m._kick(cut["series_id"])

    async def _fetch_single(self, cut: dict, root: str) -> None:
        """The single video of the slot, again and uncapped, into a folder of its own."""
        folder = os.path.join(root, str(cut["series_id"]), CUTS_DIR, f"v{cut['id']}")
        path = os.path.join(folder, f"{cut['series_id']}_{cut_tag(cut['season'], cut['episode'], cut['part'])}.mp4")
        if storage.busy(folder):
            self._m._when_free(folder, cut["series_id"])
            return
        if not await self._repo.cut_step(cut["id"], T.FETCH, work_dir=folder, out_path=path):
            return
        await self._m._io(lambda: os.makedirs(folder, exist_ok=True), owns=(folder,))
        await self._announce(await self._repo.cut(cut["id"]))
        self._m.send_command("downloads.fetch", {"key": f"{SINGLE_KEY}{cut['id']}", "url": cut["cut"]["url"], "path": path,
                                                 "series_id": cut["series_id"]})

    async def single_fetched(self, cut_id: int, payload: dict) -> None:
        """The single video of a slot came back (or did not): the cut is done and offered, or it failed."""
        cut = await self._repo.cut(cut_id)
        if cut is None or cut["status"] != "downloading":
            if payload.get("ok") and payload.get("path") and cut is None:
                await self._m._cleanup.discard(os.path.dirname(payload["path"]), kind="tree")
            return
        if payload.get("ok"):
            await self._repo.cut_step(cut_id, T.CUT, error=None)
            await self._announce(await self._repo.cut(cut_id))
            self._m._kick(cut["series_id"])
        else:
            await self._fail(cut_id, str(payload.get("error") or reason("download_failed")))

    async def file_finished(self, file_id: int) -> None:
        """The file of a compilation ended its transfer (the repository moved its cuts on): tell the windows, go on."""
        for cut in await self._repo.cuts_by("file_id", file_id):
            await self._announce(cut)
            self._m._kick(cut["series_id"])

    # --- the service --------------------------------------------------------------------------------------------

    def blocks(self, cuts: list[dict]) -> set[Slot]:
        """The slots whose voice-overs wait: a cut of theirs has not ended."""
        return {(each["season"], each["episode"], each["part"]) for each in cuts if each["status"] in LIVE}

    async def advance(self, series: dict, references: Mapping[Slot, tuple], series_cuts: list[dict]) -> bool:
        """Send the ready cuts whose REF 2 is on disk, by file of compilation; offer the cut videos. True when the service
        was certainly not reached and another try is owed."""
        owed = False
        for cut in [each for each in series_cuts if each["status"] == "done"]:
            await self._offer(series, cut)
        ready: dict[int, list[tuple[dict, str]]] = {}
        for cut in series_cuts:
            if cut["status"] != "ready" or cut["file_id"] is None:
                continue
            slot = (cut["season"], cut["episode"], cut["part"])
            current = references.get(slot)
            if current is None or current[1] != cut["ref_uid"]:
                continue  # REF 2 is not on disk, or another video holds the slot: the next plan settles it
            ready.setdefault(cut["file_id"], []).append((cut, current[0]))
        for file_id, batch in ready.items():
            owed = await self._submit(series, file_id, batch) or owed
        return owed

    async def _submit(self, series: dict, file_id: int, batch: list[tuple[dict, str]]) -> bool:
        file = await self._repo.file(file_id)
        if file is None or not file["temp_path"] or not await self._m._io(os.path.isfile, file["temp_path"]):
            if file is not None:
                await self._repo.file_lost(file_id)
            return False
        folder = (await self._m._folders())["conform"]
        if not folder:
            self._m.log.warning("сериал %d: разбор компиляции не начат: рабочая папка не задана", series["id"])
            return False
        work = os.path.join(folder, str(series["id"]), CUTS_DIR, f"f{file_id}")
        out_dir = os.path.join(work, OUT_DIR)
        await self._repo.set_file_work_dir(file_id, work)  # the folder goes with the file
        episodes = []
        sent: list[tuple[dict, str]] = []
        for cut, reference in batch:
            key = uuid.uuid4().hex[:12]
            label = f"{series['name']} {cut_tag(cut['season'], cut['episode'], cut['part'])}"
            # The folder of the episode is the label of its job: the key in it finds the job again.
            episode_dir = os.path.join(work, f"{_safe(label)} #{key}")
            copy = os.path.join(episode_dir, f"{series['id']}_{cut_tag(cut['season'], cut['episode'], cut['part'])}"
                                             f"{os.path.splitext(reference)[1]}")
            if storage.busy(episode_dir) or storage.busy(out_dir):
                self._m._when_free(episode_dir, series["id"])
                continue
            if not await self._repo.cut_step(cut["id"], T.SEND, submit_key=key,
                                             work_dir=episode_dir, job_id=None):
                continue
            try:
                await self._m._io(lambda: [os.makedirs(each, exist_ok=True) for each in (episode_dir, out_dir)],
                                  owns=(episode_dir, out_dir))
                await self._m._io(shutil.copyfile, reference, copy, timeout=COPY_TIMEOUT_S, owns=(episode_dir,))
            except Exception:
                await self._repo.cut_step(cut["id"], T.UNSEND, submit_key=None)
                await self._m._io(shutil.rmtree, episode_dir, True, owns=(episode_dir,))
                raise
            held = await container.contents(reference)
            episodes.append({"path": copy, "atrack": held.own_track if held else 0})
            sent.append((cut, key))
        if not sent:
            return False
        for cut, _ in sent:
            await self._announce(await self._repo.cut(cut["id"]))
        label = f"{series['name']} {sent[0][0]['cut']['start']}–{sent[0][0]['cut']['end']}"
        keys = [key for _, key in sent]
        self._m._sending.update(keys)
        try:
            reply = ReverseReply.model_validate(await self._m.request("conform.reverse", {
                "compilation": file["temp_path"], "episodes": episodes, "out_dir": out_dir, "label": label}))
        except (BusRequestError, asyncio.TimeoutError, ValidationError) as exc:
            self._m.log.warning("%s: ответа на разбор нет, спросим сервис: %s", label, exc)
            reply = ReverseReply(outcome=SubmitOutcome.UNCERTAIN, reason=reason("conform_no_answer", error=str(exc)))
        finally:
            self._m._sending.difference_update(keys)
        if reply.outcome is SubmitOutcome.UNAVAILABLE:
            for cut, key in sent:
                await self._release(cut, key)
            self._m.log.info("%s: разбор ждёт сервис выравнивания: %s", label, describe(reply.reason))
            return reply.retry
        if reply.outcome is SubmitOutcome.REJECTED:
            for cut, _ in sent:
                await self._fail(cut["id"], reply.reason or reason("cut_failed"))
            self._m.log.warning("%s: разбор отклонён: %s", label, describe(reply.reason))
            return False
        for cut, key in sent:
            job_id = reply.jobs.get(key)
            if job_id is not None:
                await self._attach(key, job_id)
            else:
                self.await_submission(key)
        return False

    async def _release(self, cut: dict, key: str) -> None:
        """A batch the service certainly did not get: the cut is ready again and its folder goes."""
        found = await self._repo.cut(cut["id"])
        if found is None or found["submit_key"] != key:
            return
        await self._repo.cut_step(cut["id"], T.UNSEND, submit_key=None, work_dir=None)
        if found["work_dir"]:
            await self._m._io(shutil.rmtree, found["work_dir"], True, owns=(found["work_dir"],))
        await self._announce(await self._repo.cut(cut["id"]))

    async def _attach(self, key: str, job_id: str) -> None:
        if await self._repo.attach_cut_job(key, job_id):
            self._m._watched.add(job_id)
            for cut in await self._repo.cuts_by("job_id", job_id):
                await self._announce(cut)
            unclaimed = self._m._unclaimed.pop(job_id, None)
            if unclaimed is not None:
                await self.apply(unclaimed)
            else:
                self.await_job(job_id)

    def await_submission(self, key: str, *, now: bool = False) -> None:
        self._m._recovery.want(f"{CUT_KEY}submission:{key}", lambda: self._recover(key), now=now)

    def await_job(self, job_id: str, *, now: bool = False) -> None:
        self._m._recovery.want(f"{CUT_KEY}job:{job_id}", lambda: self._reconcile(job_id), now=now)

    async def resume(self) -> None:
        """What the database says is unfinished gets its own account of attempts (at the start of the module)."""
        for key in await self._repo.cut_submissions_open():
            self.await_submission(key, now=True)
        for job_id in await self._repo.cut_jobs_open():
            self.await_job(job_id, now=True)

    async def _recover(self, key: str) -> bool:
        """A batch sent without an answer: the service names the job of the key, or it never got it."""
        if key in self._m._sending:
            return False
        try:
            reply = await self._m.request("conform.job.find", {"key": key})
        except (BusRequestError, asyncio.TimeoutError):
            return False
        if not reply.get("reachable"):
            return False
        cuts = [each for each in await self._repo.cuts_by("submit_key", key) if each["status"] == "submitting"]
        if reply.get("job_id"):
            if cuts:
                await self._attach(key, reply["job_id"])
            return True
        for cut in cuts:
            await self._release(cut, key)
            self._m._kick(cut["series_id"])
        return True

    async def _reconcile(self, job_id: str) -> bool:
        try:
            job = AlignmentJob.model_validate(await self._m.request("conform.job.get", {"job_id": job_id}))
        except (BusRequestError, asyncio.TimeoutError, ValidationError):
            return False
        if job.status is JobStatus.GONE:
            for cut in await self._repo.cuts_by("job_id", job_id):
                if cut["status"] == "aligning":
                    await self._repo.cut_step(cut["id"], T.JOB_GONE, job_id=None, submit_key=None)
                    await self._announce(await self._repo.cut(cut["id"]))
                    self._m._kick(cut["series_id"])
            return True
        if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
            await self.apply(job)
            return True
        return False

    async def apply(self, job: AlignmentJob) -> bool:
        """Take the end of a job into the cut that waits for it; False when none does."""
        cuts = [each for each in await self._repo.cuts_by("job_id", job.job_id) if each["status"] == "aligning"]
        if not cuts:
            return False
        for cut in cuts:
            result = job.results[0] if job.results else None
            # The service's contract for a cut: the video is judged by `ok` and `critical` — with a critical finding its
            # timeline cannot be trusted, and it would carry every track of the episode (reverse_api.md, 8.2).
            if job.status is JobStatus.DONE and result is not None and result.ok and result.out_path and result.critical:
                await self._fail(cut["id"], reason("cut_critical", text="; ".join(result.critical)))
            elif job.status is JobStatus.DONE and result is not None and result.ok and result.out_path:
                await self._repo.cut_step(cut["id"], T.CUT, out_path=result.out_path,
                                          from_ref_s=result.from_ref_s, from_dub_s=result.from_dub_s, error=None)
                self._m.log.info("сериал %d, s%02de%02d: серия вырезана из компиляции — из компиляции %.0f с, из серии %.0f с",
                                 cut["series_id"], cut["season"], cut["episode"], result.from_ref_s or 0,
                                 result.from_dub_s or 0)
            else:
                # The words of the service are kept as they came: the window shows them as such (`external`).
                await self._fail(cut["id"], (result.error if result is not None and result.error else None) or job.error
                                 or reason("cut_failed"))
            await self._announce(await self._repo.cut(cut["id"]))
            self._m._kick(cut["series_id"])
        return True

    async def _fail(self, cut_id: int, error: str) -> None:
        """The cut failed: the copy of REF 2 goes; its job stays in the service's history for the owner (Р-75)."""
        await self._repo.cut_step(cut_id, T.FAIL, error=error)
        await self._repo.hand_over_cut(cut_id, copy=True, job=False)
        await self._m._cleanup.work_off()
        cut = await self._repo.cut(cut_id)
        self._m.log.warning("сериал %d, s%02de%02d: серия не вырезана из компиляции, остаётся отдельная серия: %s",
                            cut["series_id"], cut["season"], cut["episode"], describe(error))
        await self._announce(cut)
        self._m._replan_after_cut(cut["series_id"])  # the episode keeps its single video and its voice: the voice-overs follow

    async def _offer(self, series: dict, cut: dict) -> None:
        """The video of the cut goes to the library for the next build of the container; refused, it goes to the journal and
        the next sync plans the cut again if the plan still wants it."""
        items = await self._items(series["id"])
        item = items.get((cut["season"], cut["episode"], cut["part"]))
        data = cut["cut"]
        offered = {"key": data["key"], "height": data.get("height"), "voice_group": data.get("voice_group") or "",
                   "source_id": data["source_id"], "player": data["player"], "start": data.get("start"),
                   "end": data.get("end"), "from_ref_s": cut["from_ref_s"], "from_dub_s": cut["from_dub_s"]} \
            if data["key"] else None
        if item is None or item["unique_id"] != cut["ref_uid"]:
            answer = {"accepted": False}
        else:
            try:
                answer = await self._m.request("downloads.video.offer", {
                    "series_id": series["id"], "unique_id": cut["ref_uid"], "path": cut["out_path"], "cut": offered})
            except (BusRequestError, asyncio.TimeoutError) as exc:
                self._m.log.warning("сериал %d, s%02de%02d: видео разбора не передано, повторим: %s", series["id"],
                                    cut["season"], cut["episode"], exc)
                return
        if answer.get("accepted"):
            await self._repo.cut_step(cut["id"], T.OFFER)
            # The video is the library's now: the copy of REF 2 goes; the job (with the folder of its plots) leaves the
            # service's history when the setting asks it to, else when the owner deletes it there (`job_removed`).
            await self._repo.hand_over_cut(cut["id"], copy=True, job=await self._m._clear_finished())
        else:
            self._m.log.info("сериал %d, s%02de%02d: видео разбора не принято: %s", series["id"], cut["season"],
                             cut["episode"], describe(answer.get("reason")))
            await self._repo.drop_cuts(series["id"], {(each["season"], each["episode"], each["part"])
                                                      for each in await self._repo.cuts_of(series["id"])
                                                      if each["id"] != cut["id"]})
            self._m._want_plan(series["id"])
        await self._m._cleanup.work_off()
        await self._announce(await self._repo.cut(cut["id"]) or {**cut, "status": "removed"})

    async def job_removed(self, job_id: str) -> None:
        """The service dropped the record of a job of a cut: the folder it wrote in goes."""
        for cut in await self._repo.cuts_by("job_id", job_id):
            if cut["status"] in ("offered", "failed"):
                await self._repo.hand_over_cut(cut["id"], copy=False, job=True)
        await self._m._cleanup.work_off()

    async def retry(self, series_id: int, season: int, episode: int | None, part: int | None) -> int:
        """The user's word: the failed cuts of the scope are planned again and admitted now."""
        restarted = 0
        for cut in await self._repo.cuts_of(series_id):
            if cut["status"] != "failed" or cut["season"] != season or (episode is not None and cut["episode"] != episode) \
                    or (part is not None and cut["part"] != part):
                continue
            await self._repo.hand_over_cut(cut["id"], copy=True, job=True)
            if await self._repo.cut_step(cut["id"], T.RETRY, error=None, file_id=None, admitted=1,
                                         submit_key=None, job_id=None, out_path=None):
                restarted += 1
                await self._announce(await self._repo.cut(cut["id"]))
        if restarted:
            await self._m._cleanup.work_off()
            self._m._want_plan(series_id)
        return restarted

    # --- what the windows see -----------------------------------------------------------------------------------

    def record(self, cut: dict) -> dict[str, Any]:
        data = cut.get("cut") or {}
        return {"series_id": cut["series_id"], "season": cut["season"], "episode": cut["episode"], "part": cut["part"],
                "cut_key": cut.get("cut_key") or data.get("key"), "status": cut["status"],
                "from_ref_s": cut.get("from_ref_s"), "from_dub_s": cut.get("from_dub_s"),
                "detail": _parse(cut.get("error"))}

    async def _announce(self, cut: dict | None) -> None:
        if cut is None:
            return
        self._m.publish_event("voices.cut.changed", {**self.record(cut), "rev": self._m._next_rev(),
                                                     "epoch": await self._m._generation()})

    async def _announce_slot(self, series_id: int, slot: Slot) -> None:
        for cut in await self._repo.cuts_of(series_id):
            if (cut["season"], cut["episode"], cut["part"]) == slot:
                await self._announce(cut)

    async def snapshot(self) -> list[dict[str, Any]]:
        return [self.record(each) for each in await self._repo.cuts_of()]


COPY_TIMEOUT_S = 1800.0


def _safe(label: str) -> str:
    """A label as a folder name: no separator of paths, no `#` but the one before the key."""
    return label.replace(os.sep, " ").replace("#", " ").strip()


def _parse(stored: str | None):
    return parse(stored)
