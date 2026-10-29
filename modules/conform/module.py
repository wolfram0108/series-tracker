"""Module conform: bridge to the alignment daemon (track-muxer), mission voice-alignment, stages 5 and 7.

Mirrors the daemon's `conform` registry from its event stream (snapshot, changed, progress) and
pushes it to the interface, queues episode jobs for the voices module and reports how a job ended.
The daemon owns the state; this module keeps no database. Without a daemon address
(`ST_CONFORM_URL`) the module is idle. Paths cross the boundary through the setting
`conform.path_map` (JSON {local prefix: daemon prefix}, default {"/nas": "/mnt/meganas/media"}).

Query:
  conform.status {} -> {configured, daemon: {up, version, device, gpu, gpu_name, error}, tasks: [...]}
  conform.submit {ref, dubs: [path], out_dir, cache_dir, label?, key?}
      -> {job_id} | {rejected: reason} | {unavailable: reason, retry: bool} | {uncertain: reason}
      One episode: a reference video and the voice-over files to align to it, local paths. `key`
      is written into the job's label so that the job can be found again when the answer is lost.
      `rejected` is final (a path outside the shared storage, a refusal of the daemon). `uncertain`:
      the request may have reached the daemon (the answer was lost): the caller keeps the key and asks
      conform.job.find, it does not send again.
      `unavailable` is not final and the request certainly did not leave: with retry=false the daemon is
      not configured or down and the caller waits for conform.daemon.up; with retry=true the connection
      failed before the request was sent and asking again soon may work.
  conform.workspace {dub_folder?, reference_folder?} -> {out_dir, cache_dir, scratch}   only what is asked for:
      the folders the daemon is asked to write the aligned tracks and its cache to for the voice-overs in
      dub_folder, and the scratch folder it leaves next to a reference video. Pure: no daemon is asked.
  conform.cancel {job_id} -> {done: bool} — stop a job whose result nobody needs any more; `done` is
      true when it is stopped or the daemon does not know it, false when the daemon could not be reached.
  conform.job.find {key} -> {reachable: bool, job_id: str | None} — the job sent with this key.
  conform.job.get {job_id} -> {job_id, status, error, results}   status `gone` when the daemon
      no longer knows the job; results as in conform.job.finished.
Event:
  conform.queue.changed {daemon, tasks}   the whole picture on every change and whenever an
                                          interface connects (gateway.sse.clients)
  conform.job.finished {job_id, status, error, results: [{dub, ok, skipped, out_path, suspect,
      critical, warnings, resid_ms, error}]}   once per job that ended (done, failed, cancelled);
      out_path is a local path
  conform.daemon.up {}                    the daemon became reachable: waiting work may be sent
A task is the compact view of one daemon job: id, label, status, progress, stage, stage_pct,
detail, dub_index, dub_total, cur_dub, elapsed_s, error and results[{dub, ok, skipped, resid_ms,
coverage, cuts, suspect, warnings, critical, error}].
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from core import BaseModule, BusRequestError
from core.contracts import AlignmentJob, DubResult, JobStatus
from core.envelope import Envelope
from core.reasons import describe, reason

from .client import DaemonClient, DaemonRefused
from .paths import DEFAULT_PATH_MAP, reverse, translate, workspace

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0
PATH_MAP_KEY = "conform.path_map"
ENDED = ("done", "failed", "cancelled")
# Failures before the request left this machine: the daemon has certainly not seen it.
NOT_DELIVERED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

_JOB_FIELDS = ("id", "label", "status", "progress", "stage", "stage_pct", "detail", "dub_index",
               "dub_total", "cur_dub", "elapsed_s", "error", "created_at", "updated_at")


def compact_job(job: dict[str, Any]) -> dict[str, Any]:
    """The part of a daemon job the interface shows; traces, plots and operation logs stay behind."""
    view = {field: job.get(field) for field in _JOB_FIELDS}
    view["results"] = [{
        "dub": result.get("dub"), "ok": bool(result.get("ok")), "skipped": bool(result.get("skipped")),
        "resid_ms": result.get("audio_resid_ms"), "coverage": result.get("audio_coverage"),
        "cuts": result.get("audio_cuts"), "suspect": bool(result.get("suspect")),
        "warnings": list(result.get("warnings") or []), "critical": list(result.get("critical") or []),
        "error": result.get("error"),
    } for result in job.get("results") or []]
    return view


def job_label(label: str | None, key: str | None) -> str | None:
    """The label the job carries: the human one and, when given, the key that finds the job again."""
    return f"{label or ''} #{key}".strip() if key else label


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
            dub=result.get("dub"), ok=bool(result.get("ok")), skipped=bool(result.get("skipped")),
            out_path=local(result.get("out_path")), suspect=bool(result.get("suspect")),
            critical=list(result.get("critical") or []), warnings=list(result.get("warnings") or []),
            resid_ms=result.get("audio_resid_ms"), error=result.get("error"),
        ) for result in job.get("results") or []]).model_dump(mode="json")


class ConformModule(BaseModule):
    name = "conform"

    def __init__(self, bus, client: DaemonClient | None = None,
                 backoff_start: float = BACKOFF_START_S) -> None:
        self._client = client
        self._backoff_start = backoff_start
        self._jobs: dict[str, dict[str, Any]] = {}
        self._announced: set[str] = set()
        self._daemon: dict[str, Any] = {"up": False, "error": None}
        self._runner: asyncio.Task | None = None
        super().__init__(bus)

    def register(self) -> None:
        self.handle("conform.status", self.on_status)
        self.handle("conform.submit", self.on_submit)
        self.handle("conform.job.get", self.on_job_get)
        self.handle("conform.workspace", self.on_workspace)
        self.handle("conform.cancel", self.on_cancel)
        self.handle("conform.job.find", self.on_job_find)
        self.handle("gateway.sse.clients", self.on_clients)

    def snapshot(self) -> dict[str, Any]:
        tasks = sorted(self._jobs.values(), key=lambda job: job.get("seq") or 0)
        return {"configured": self._client is not None, "daemon": dict(self._daemon),
                "tasks": [compact_job(job) for job in tasks]}

    async def on_status(self, env: Envelope) -> dict:
        return self.snapshot()

    async def _path_map(self) -> dict[str, str]:
        try:
            reply = await self.request("settings.value.get", {"key": PATH_MAP_KEY}, timeout=10)
            value = json.loads(reply.get("value") or "null")
        except (BusRequestError, asyncio.TimeoutError, json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, dict) and value and all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            return value
        return dict(DEFAULT_PATH_MAP)

    async def on_submit(self, env: Envelope) -> dict:
        if self._client is None:
            return {"unavailable": reason("conform_not_configured"), "retry": False}
        if not self._daemon.get("up"):
            return {"unavailable": reason("conform_down", error=self._daemon.get("error") or ""), "retry": False}
        payload = env.payload
        mapping = await self._path_map()
        try:
            body = {"ref": translate(payload["ref"], mapping),
                    "dubs": [translate(path, mapping) for path in payload["dubs"]],
                    "out_dir": translate(payload["out_dir"], mapping),
                    "cache_dir": translate(payload["cache_dir"], mapping),
                    "keep_tmp": False, "label": job_label(payload.get("label"), payload.get("key"))}
            job = await self._client.enqueue(body)
        except (ValueError, DaemonRefused) as exc:
            self.log.warning("conform: задача %s отклонена: %s", payload.get("label") or "", describe(str(exc)))
            return {"rejected": str(exc)}
        except httpx.HTTPError as exc:
            problem = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            if isinstance(exc, NOT_DELIVERED):
                self.log.warning("conform: задача %s не поставлена: %s", payload.get("label") or "", problem)
                return {"unavailable": reason("conform_unreachable", error=problem), "retry": True}
            # The request may have reached the daemon: the caller must ask by the key, not send again.
            self.log.warning("conform: ответа на постановку %s нет: %s", payload.get("label") or "", problem)
            return {"uncertain": reason("conform_no_answer", error=problem)}
        self.log.info("conform: поставлена задача %s (%s), озвучек %d", job.get("id"),
                      payload.get("label") or "", len(body["dubs"]))
        return {"job_id": job["id"]}

    async def on_workspace(self, env: Envelope) -> dict:
        return workspace(env.payload.get("dub_folder"), env.payload.get("reference_folder"))

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
        if job is None and self._client is not None and self._daemon.get("up"):
            job = await self._client.job(job_id)
        if job is None:
            return AlignmentJob(job_id=job_id, status=JobStatus.GONE).model_dump(mode="json")
        return finished_view(job, reverse(await self._path_map()))

    async def on_clients(self, env: Envelope) -> None:
        # A new interface has no history: it gets the whole picture at once.
        if env.payload.get("count"):
            self._publish()

    async def on_start(self) -> None:
        if self._client is not None:
            self._runner = asyncio.create_task(self._run(), name="conform:feed")

    async def on_stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
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
        elif name == "conform.progress":
            job = self._jobs.get(payload.get("id"))
            if job is not None:
                job.update({k: v for k, v in payload.items() if k != "id"})
