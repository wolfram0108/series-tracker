"""Module conform: bridge to the alignment daemon (track-muxer), mission voice-alignment, stage 5.

Mirrors the daemon's `conform` registry from its event stream (snapshot, changed, progress) and
pushes it to the interface. The daemon owns the state; this module keeps no database and queues
nothing at this stage. Without a daemon address (`ST_CONFORM_URL`) the module is idle.

Query:
  conform.status {} -> {configured, daemon: {up, version, device, gpu, gpu_name, error}, tasks: [...]}
Event:
  conform.queue.changed {daemon, tasks}   the whole picture on every change and whenever an
                                          interface connects (gateway.sse.clients)
A task is the compact view of one daemon job: id, label, status, progress, stage, stage_pct,
detail, dub_index, dub_total, cur_dub, elapsed_s, error and results[{dub, ok, skipped, resid_ms,
coverage, cuts, suspect, warnings, critical, error}].
"""
from __future__ import annotations

import asyncio
from typing import Any

from core import BaseModule
from core.envelope import Envelope

from .client import DaemonClient

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0

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


class ConformModule(BaseModule):
    name = "conform"

    def __init__(self, bus, client: DaemonClient | None = None,
                 backoff_start: float = BACKOFF_START_S) -> None:
        self._client = client
        self._backoff_start = backoff_start
        self._jobs: dict[str, dict[str, Any]] = {}
        self._daemon: dict[str, Any] = {"up": False, "error": None}
        self._runner: asyncio.Task | None = None
        super().__init__(bus)

    def register(self) -> None:
        self.handle("conform.status", self.on_status)
        self.handle("gateway.sse.clients", self.on_clients)

    def snapshot(self) -> dict[str, Any]:
        tasks = sorted(self._jobs.values(), key=lambda job: job.get("seq") or 0)
        return {"configured": self._client is not None, "daemon": dict(self._daemon),
                "tasks": [compact_job(job) for job in tasks]}

    async def on_status(self, env: Envelope) -> dict:
        return self.snapshot()

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
                self.log.info("conform: подключён к демону %s", self._client.base_url)
                async for name, payload in self._client.events():
                    delay = self._backoff_start
                    self._apply(name, payload)
                    self._publish()
                reason = "поток событий закрыт демоном"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport failure means "daemon unreachable"
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self._daemon = {"up": False, "error": reason}
            self._jobs.clear()
            self._publish()
            self.log.warning("conform: демон недоступен (%s), повтор через %.0f с", reason, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

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
