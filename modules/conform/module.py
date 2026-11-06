"""Module conform: bridge to the alignment daemon (track-muxer), mission voice-alignment, stages 5 and 7.

Mirrors the daemon's `conform` registry from its event stream (snapshot, changed, progress) and
pushes it to the interface, queues episode jobs for the voices module and reports how a job ended.
The daemon owns the state; this module keeps no database. The daemon address is the setting `conform.url`
(`ST_CONFORM_URL` while none is stored; without an address the module is idle) and a saved address is taken at once,
without a restart. The work folder is two settings, `conform.work_dir` (as this server names it) and
`conform.work_dir_remote` (the same folder as the daemon names it); no default, the owner names both. Everything the
daemon is given or writes lies inside it, and the paths cross the boundary through these two names.

Query:
  conform.submit {ref, dubs: [path], out_dir, cache_dir, label?, key?} -> SubmitReply (core/contracts):
      {outcome, job_id?, reason?, retry}
      One episode: a reference video and the voice-over files to align to it, local paths. `key`
      is written into the job's label so that the job can be found again when the answer is lost.
      The outcome is what was proved. `accepted`: the daemon named the job. `rejected` is final for this request: a path
      outside the shared storage, found before anything is sent, or a refusal of the daemon (it judged no voice-over:
      the voices module offers them again at the next sync). `unavailable`:
      the request certainly did not take effect: with retry=false the daemon is not configured, has no path map or is down
      and the caller waits for conform.daemon.up; with retry=true the connection failed before the request was
      sent (or the daemon asked to come back later) and asking again soon may work. `uncertain`: the request
      was sent and nothing well-formed came back, so the daemon may have the job; the caller keeps the key
      and asks conform.job.find, it does not send again.
  conform.settings.get {} -> ConformSettingsView {url, work_dir, work_dir_remote, clear_finished}: what applies now.
      `clear_finished` (the setting conform.clear_finished, "true" or "false", written by the pipeline flag
      `/api/settings/conform_clear_finished` of the debug tab): a job that ended and whose result was taken leaves the
      daemon's history on its own, unless it ended in an error; off, the history keeps it until the owner deletes it.
  conform.settings.set {url, work_dir, work_dir_remote} -> ConformSettingsSaved {saved, problems, settings}: checked as a whole, written
      through settings.values.set_many and taken at once, or refused with coded problems and nothing written.
  conform.workspace {} -> {downloads, conform}: the two folders inside the work folder, where voice-overs wait for
      alignment and where the service works; both empty while no work folder is named.
  conform.workspace {dub_folder} -> {out_dir, cache_dir}: the folders the daemon is asked to write the aligned
      tracks and its cache to for an episode folder of the service. Pure: no daemon is asked.
  conform.cancel {job_id} -> {done: bool} — stop a job that waits or runs (its record stays as `cancelled`), or delete the
      record of a job that ended: the daemon then drops the files it keeps for the record (graphs, journal, time map,
      cache) and leaves the output audio. `done` is true when it is stopped or the daemon does not know it,
      false when the daemon could not be reached.
  conform.plot {job_id, name} -> {found: bool, data?: base64 PNG} — the picture of a plot named in a result (`plot`); only
      a plain PNG name is asked of the daemon.
  conform.job.find {key} -> {reachable: bool, job_id: str | None} — the job sent with this key.
  conform.job.get {job_id} -> {job_id, status, error, results}   status `gone` only when the daemon was
      asked and does not know the job; when it cannot be asked the query fails; results as in
      conform.job.finished.
Event:
  conform.queue.changed {daemon, tasks}   the whole picture on every change and whenever an
                                          interface connects (gateway.sse.clients)
  conform.job.finished {job_id, status, error, results: [{dub, ok, refused, skipped, out_path, suspect,
      critical, warnings, resid_ms, error}]}   once per job that ended (done, failed, cancelled);
      out_path is a local path. A pair that is not `ok` is `refused` only when the service itself turned it down (its trace
      holds a decision that went `abort`); otherwise it crashed in the service and no judgement of the voice-over was made.
      The results of a failed or stopped job are those of the pairs it had finished.
  conform.daemon.up {}                    the daemon became reachable: waiting work may be sent
  conform.job.removed {job_id}            the daemon dropped the record of a job (deleted by the owner, by the service
                                          or by the cleanup of this project)
  conform.settings.changed ConformSettingsView   after every save, so that every open interface shows it
A task is the compact view of one daemon job: id, label (the human part), key (the one the label carries,
if any), status, progress, stage, stage_pct,
detail, dub_index, dub_total, cur_dub, dubs (file names), elapsed_s, error and results[{dub, plot, ok, refused, skipped, resid_ms,
coverage, cuts, suspect, warnings, critical, error}]; `plot` names the picture of the voice-over's plot, if the daemon made one.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
from collections.abc import Callable
from typing import Any

import httpx

from core import BaseModule, BusRequestError
from core.contracts import (AlignmentJob, ConformSettingsDraft, ConformSettingsSaved, DubResult, JobStatus,
                            SubmitReply)
from core.envelope import Envelope
from core.reasons import describe, reason

from .client import DaemonClient, DaemonRefused, NotDelivered, Unconfirmed
from . import settings as conform_settings
from .paths import reverse, roots, translate, workspace
from .settings import CLEAR_KEY, REMOTE_DIR_KEY, URL_KEY, WORK_DIR_KEY, mapping_of

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0
ENDED = ("done", "failed", "cancelled")
# The word of the service's trace for a gate that turns a pair down (the one gate there is: a foreign video).
ABORT = "abort"

_JOB_FIELDS = ("id", "label", "status", "progress", "stage", "stage_pct", "detail", "dub_index",
               "dub_total", "cur_dub", "elapsed_s", "error", "created_at", "updated_at")


def compact_job(job: dict[str, Any]) -> dict[str, Any]:
    """The part of a daemon job the interface shows; traces, plots and operation logs stay behind."""
    view = {field: job.get(field) for field in _JOB_FIELDS}
    view["label"], view["key"] = split_label(job.get("label"))
    view["dubs"] = [os.path.basename(str(dub)) for dub in job.get("dubs") or []]
    view["results"] = [{
        "dub": result.get("dub"), "plot": plot_of(result),
        "ok": bool(result.get("ok")), "refused": refused_by_service(result), "skipped": bool(result.get("skipped")),
        "resid_ms": result.get("audio_resid_ms"), "coverage": result.get("audio_coverage"),
        "cuts": result.get("audio_cuts"), "suspect": bool(result.get("suspect")),
        "warnings": list(result.get("warnings") or []), "critical": list(result.get("critical") or []),
        "error": result.get("error"),
    } for result in job.get("results") or []]
    return view


def refused_by_service(result: dict[str, Any]) -> bool:
    """The service itself turned the pair down: its trace holds a decision that went the way of `abort`. A pair that is not `ok`
    without such a decision crashed in the service (its trace may end in an `exception`, or hold nothing): no judgement of
    the voice-over, only a failure to make one."""
    return not result.get("ok") and any(
        record.get("kind") == "decision" and record.get("verdict") == ABORT for record in result.get("trace") or [])


def plot_of(result: dict[str, Any]) -> str | None:
    """The name of the picture of a voice-over's alignment plot; the interactive page of it is not shown."""
    return next((str(ref["name"]) for ref in result.get("plots") or []
                 if ref.get("kind") == "track" and str(ref.get("name") or "").endswith(".png")), None)


def job_label(label: str | None, key: str | None) -> str | None:
    """The label the job carries: the human one and, when given, the key that finds the job again."""
    return f"{label or ''} #{key}".strip() if key else label


_KEY_SUFFIX = re.compile(r"(?:^|\s+)#(\S+)$")
_JOB_ID = re.compile(r"[\w-]+")
_PLOT_NAME = re.compile(r"[\w.-]+\.png")


def split_label(label: str | None) -> tuple[str | None, str | None]:
    """The inverse of job_label: the human part of a job's label and the key it carries, if any."""
    found = _KEY_SUFFIX.search(label or "")
    return ((label or "")[:found.start()], found.group(1)) if found else (label, None)


def finished_view(job: dict[str, Any], to_local: dict[str, str]) -> dict[str, Any]:
    """How a job ended, with the output paths translated back to this server."""
    def local(path: str | None) -> str | None:
        if not path:
            return None
        try:
            return translate(path, to_local)
        except ValueError:
            return path

    return AlignmentJob(
        job_id=job.get("id"), status=job.get("status"), error=job.get("error"),
        results=[DubResult(
            dub=result.get("dub"), ok=bool(result.get("ok")), refused=refused_by_service(result),
            skipped=bool(result.get("skipped")),
            out_path=local(result.get("out_path")), suspect=bool(result.get("suspect")),
            critical=list(result.get("critical") or []), warnings=list(result.get("warnings") or []),
            resid_ms=result.get("audio_resid_ms"), error=result.get("error"),
        ) for result in job.get("results") or []]).model_dump(mode="json")


class ConformModule(BaseModule):
    name = "conform"

    def __init__(self, bus, client: DaemonClient | None = None, backoff_start: float = BACKOFF_START_S, *,
                 default_url: str = "", client_factory: Callable[[str], DaemonClient] = DaemonClient) -> None:
        # A ready client is the address in force from the start; without one the address is read at the start.
        self._client = client
        self._client_factory = client_factory
        self._default_url = default_url
        self._address_lock = asyncio.Lock()
        self._backoff_start = backoff_start
        self._jobs: dict[str, dict[str, Any]] = {}
        self._announced: set[str] = set()
        self._daemon: dict[str, Any] = {"up": False, "error": None}
        self._runner: asyncio.Task | None = None
        super().__init__(bus)

    def register(self) -> None:
        self.handle("conform.submit", self.on_submit)
        self.handle("conform.job.get", self.on_job_get)
        self.handle("conform.workspace", self.on_workspace)
        self.handle("conform.cancel", self.on_cancel)
        self.handle("conform.plot", self.on_plot)
        self.handle("conform.settings.get", self.on_settings_get)
        self.handle("conform.settings.set", self.on_settings_set)
        self.handle("conform.job.find", self.on_job_find)
        self.handle("gateway.sse.clients", self.on_clients)

    def snapshot(self) -> dict[str, Any]:
        tasks = sorted(self._jobs.values(), key=lambda job: job.get("seq") or 0)
        return {"daemon": dict(self._daemon), "tasks": [compact_job(job) for job in tasks]}

    async def _stored(self, key: str) -> str | None:
        try:
            reply = await self.request("settings.value.get", {"key": key})
        except (BusRequestError, asyncio.TimeoutError):
            return None
        return reply.get("value")

    async def _path_map(self) -> dict[str, str]:
        return mapping_of(await self._stored(WORK_DIR_KEY), await self._stored(REMOTE_DIR_KEY))

    async def on_settings_get(self, env: Envelope) -> dict:
        return (await self._settings_view()).model_dump(mode="json")

    async def on_settings_set(self, env: Envelope) -> dict:
        draft = ConformSettingsDraft.model_validate(env.payload)
        problems = conform_settings.check(draft)
        if not problems:
            draft = conform_settings.clean(draft)
            await self.request("settings.values.set_many", {"values": conform_settings.stored(draft)})
            await self._use_address(draft.url)
        view = await self._settings_view()
        if not problems:
            self.log.info("conform: настройки сервера сохранены, адрес %s", draft.url or "не задан")
            self.publish_event("conform.settings.changed", view.model_dump(mode="json"))
        return ConformSettingsSaved(saved=not problems, problems=problems, settings=view).model_dump(mode="json")

    async def _settings_view(self):
        url = self._client.base_url if self._client is not None else ""
        return conform_settings.view(url, await self._stored(WORK_DIR_KEY) or "", await self._stored(REMOTE_DIR_KEY) or "",
                                     await self._stored(CLEAR_KEY))

    async def _use_address(self, url: str) -> None:
        """Point the bridge at another daemon, or at none: the feed of the old one ends, the picture starts over."""
        async with self._address_lock:
            if url == (self._client.base_url if self._client is not None else ""):
                return
            await self._stop_feed()
            self._client = self._client_factory(url) if url else None
            self._jobs.clear()
            self._daemon = {"up": False, "error": None}
            self._publish()
            self._start_feed()

    async def on_submit(self, env: Envelope) -> dict:
        if self._client is None:
            return SubmitReply.unavailable(reason("conform_not_configured"), retry=False).model_dump()
        if not self._daemon.get("up"):
            return SubmitReply.unavailable(reason("conform_down", error=self._daemon.get("error") or ""),
                                           retry=False).model_dump()
        payload = env.payload
        label = payload.get("label") or ""
        mapping = await self._path_map()
        if not mapping:
            return SubmitReply.unavailable(reason("conform_no_work_dir"), retry=False).model_dump()
        try:  # before anything is sent: a problem here is a certain refusal
            body = self._request_body(payload, mapping)
        except ValueError as exc:
            self.log.warning("conform: задача %s отклонена: %s", label, describe(str(exc)))
            return SubmitReply.rejected(str(exc)).model_dump()
        try:
            job = await self._client.enqueue(body)
        except DaemonRefused as exc:
            self.log.warning("conform: задача %s отклонена: %s", label, describe(str(exc)))
            return SubmitReply.rejected(str(exc)).model_dump()
        except NotDelivered as exc:
            self.log.warning("conform: задача %s не поставлена: %s", label, exc)
            return SubmitReply.unavailable(reason("conform_unreachable", error=str(exc)), retry=True).model_dump()
        except Unconfirmed as exc:
            # The request may have reached the daemon: the caller must ask by the key, not send again.
            self.log.warning("conform: ответа на постановку %s нет: %s", label, exc)
            return SubmitReply.uncertain(reason("conform_no_answer", error=str(exc))).model_dump()
        self.log.info("conform: поставлена задача %s (%s), озвучек %d", job["id"], label, len(body["dubs"]))
        return SubmitReply.accepted(str(job["id"])).model_dump()

    @staticmethod
    def _request_body(payload: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
        return {"ref": translate(payload["ref"], mapping),
                "dubs": [translate(path, mapping) for path in payload["dubs"]],
                "out_dir": translate(payload["out_dir"], mapping),
                "cache_dir": translate(payload["cache_dir"], mapping),
                "keep_tmp": False, "label": job_label(payload.get("label"), payload.get("key")),
                # A reference that is a container holds several tracks: the alignment listens to the video's own sound.
                **({"ref_atrack": int(payload["ref_atrack"])} if payload.get("ref_atrack") else {})}

    async def on_workspace(self, env: Envelope) -> dict:
        if not env.payload.get("dub_folder"):
            return roots(await self._stored(WORK_DIR_KEY))
        return workspace(env.payload["dub_folder"])

    async def on_cancel(self, env: Envelope) -> dict:
        job_id = env.payload["job_id"]
        if self._client is None or not self._daemon.get("up"):
            self.log.info("conform: задачу %s отменить некому — демон недоступен", job_id)
            return {"done": False}
        try:
            known = await self._client.cancel(job_id)
        except httpx.HTTPError as exc:
            self.log.warning("conform: задача %s не отменена: %s", job_id, exc)
            return {"done": False}
        self.log.info("conform: задача %s %s", job_id, "отменена" if known else "демону неизвестна")
        return {"done": True}

    async def on_plot(self, env: Envelope) -> dict:
        """The picture of a plot of a job, taken from the daemon; the names are checked before anything is asked."""
        job_id, name = str(env.payload.get("job_id")), str(env.payload.get("name"))
        if not (_JOB_ID.fullmatch(job_id) and _PLOT_NAME.fullmatch(name)):
            return {"found": False}
        if self._client is None or not self._daemon.get("up"):
            raise RuntimeError(reason("conform_down", error=self._daemon.get("error") or ""))
        try:
            picture = await self._client.plot(job_id, name)
        except httpx.HTTPError as exc:
            raise RuntimeError(reason("conform_down", error=str(exc))) from exc
        if picture is None:
            return {"found": False}
        return {"found": True, "data": base64.b64encode(picture).decode("ascii")}

    async def on_job_find(self, env: Envelope) -> dict:
        if self._client is None or not self._daemon.get("up"):
            return {"reachable": False, "job_id": None}
        try:
            jobs = await self._client.jobs()
        except httpx.HTTPError:
            return {"reachable": False, "job_id": None}
        mark = f"#{env.payload['key']}"
        found = next((job["id"] for job in jobs if str(job.get("label") or "").endswith(mark)), None)
        return {"reachable": True, "job_id": found}

    async def on_job_get(self, env: Envelope) -> dict:
        job_id = env.payload["job_id"]
        job = self._jobs.get(job_id)
        if job is None:
            if self._client is None or not self._daemon.get("up"):
                raise RuntimeError(reason("conform_down", error=self._daemon.get("error") or ""))  # not asked is not "gone"
            job = await self._client.job(job_id)
        if job is None:
            return AlignmentJob(job_id=job_id, status=JobStatus.GONE).model_dump(mode="json")
        return finished_view(job, reverse(await self._path_map()))

    async def on_clients(self, env: Envelope) -> None:
        # A new interface has no history: it gets the whole picture at once.
        if env.payload.get("count"):
            self._publish()

    async def on_start(self) -> None:
        if self._client is None:
            stored = await self._stored(URL_KEY)
            url = self._default_url if stored is None else stored
            self._client = self._client_factory(url) if url else None
        self._start_feed()

    async def on_stop(self) -> None:
        await self._stop_feed()

    def _start_feed(self) -> None:
        if self._client is not None:
            self._runner = asyncio.create_task(self._run(), name="conform:feed")

    async def _stop_feed(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        if self._client is not None:
            await self._client.close()

    def _publish(self) -> None:
        picture = self.snapshot()
        self.publish_event("conform.queue.changed",
                           {"daemon": picture["daemon"], "tasks": picture["tasks"]})

    async def _run(self) -> None:
        assert self._client is not None
        delay = self._backoff_start
        while True:
            try:
                self._daemon = {"up": True, "error": None, **await self._client.info()}
                self._publish()
                self.publish_event("conform.daemon.up", {})
                self.log.info("conform: подключён к демону %s", self._client.base_url)
                async for name, payload in self._client.events():
                    delay = self._backoff_start
                    self._apply(name, payload)
                    self._publish()
                    await self._announce()
                problem = reason("stream_closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport failure means "daemon unreachable"
                problem = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self._daemon = {"up": False, "error": problem}
            self._jobs.clear()
            self._publish()
            self.log.warning("conform: демон недоступен (%s), повтор через %.0f с", describe(problem), delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

    async def _announce(self) -> None:
        """Report every job that ended since the last look, once."""
        ended = [job for job in self._jobs.values()
                 if job.get("status") in ENDED and job["id"] not in self._announced]
        if not ended:
            return
        to_local = reverse(await self._path_map())
        for job in ended:
            self._announced.add(job["id"])
            self.publish_event("conform.job.finished", finished_view(job, to_local))

    def _apply(self, name: str, payload: dict[str, Any]) -> None:
        if name == "conform.snapshot":
            self._jobs = {job["id"]: job for job in payload.get("items") or []}
        elif name == "conform.changed":
            for job in payload.get("items") or []:
                self._jobs[job["id"]] = job
            for job_id in payload.get("removed") or []:
                self._jobs.pop(job_id, None)
                self._announced.discard(job_id)
                self.publish_event("conform.job.removed", {"job_id": job_id})
        elif name == "conform.progress":
            job = self._jobs.get(payload.get("id"))
            if job is not None:
                job.update({k: v for k, v in payload.items() if k != "id"})
