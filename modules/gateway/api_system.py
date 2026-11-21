"""HTTP points of the «scan and queues» block (Р-20) — the shapes of routes/system.py.

/api/agent/reset is not carried over: the point was broken (finding 23) and dead on the front, and «hung statuses» are
impossible in the new system — a status is kept nowhere (Р-11).
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from calc.gateway.flags import flag_text
from calc.gateway.queues import agent_tasks
from core.module import BusRequestError
from core.contracts import DownloadCard, JobCancelled, PackCard
from core.reasons import reason, relayed
from core.topics import (CONFORM_CANCEL, CONFORM_PLOT, DOWNLOADS_CANCEL, DOWNLOADS_CARD_REMOVE, DOWNLOADS_QUEUE_CLEAR,
                         DOWNLOADS_QUEUE_GET, PLANNER_PACK_GET, PLANNER_PACK_REMOVE, SCAN_ALL_START, SCAN_SCHEDULE_SET,
                         SCAN_STATUS_GET, SETTINGS_VALUE_SET, TORRENTS_QUEUE_GET)

from .schemas import (ConformJobChange, DynamicObject, ErrorOnly, ErrorResponse, OkResponse, QueueTask, ScannerStatus,
                      ScheduleBody)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    @r.get("/api/scanner/status", response_model=ScannerStatus)
    async def scanner_status():
        return (await gw.reply_of(SCAN_STATUS_GET, {})).model_dump(mode="json")

    @r.post("/api/scanner/settings", response_model=OkResponse,
            response_model_exclude_none=True)
    async def scanner_settings(request: Request):
        data = await request.json()
        if "enabled" in data:
            await gw.reply_of(SETTINGS_VALUE_SET, {
                "key": "scanner_agent_enabled",
                "value": flag_text(data["enabled"])})
        # The schedule is counted again by scan on settings.changed (Р-20); the immediate full scan of the old system is not
        # reproduced.
        return {"success": True}

    @r.post("/api/schedule", response_model=DynamicObject, responses={400: {"model": ErrorResponse}})
    async def schedule(body: ScheduleBody):
        """The times of day of the syncs and of the checks of the torrent releases; a schedule scan refuses is 400 with
        its reason."""
        reply = await gw.reply_of(SCAN_SCHEDULE_SET, body.model_dump(mode="json"))
        if not reply.ok:
            return JSONResponse({"error": reply.reason}, status_code=400)
        return {"schedule": reply.schedule.model_dump(mode="json")}

    @r.post("/api/scanner/scan_all", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={409: {"model": ErrorResponse}})
    async def scan_all(request: Request):
        reply = await gw.reply_of(SCAN_ALL_START, {})
        if not reply.started:
            return JSONResponse(
                {"success": False, "error": reason("scan_all_running")},
                status_code=409)
        return {"success": True,
                "message": reason("sync_all_started")}

    @r.get("/api/agent/queue", response_model=list[QueueTask])
    async def agent_queue():
        try:
            reply = await gw.reply_of(TORRENTS_QUEUE_GET, {})
        except BusRequestError:
            return []  # the module is not up — as the old «no agent»
        return agent_tasks(reply["tasks"])

    @r.get("/api/downloads/queue", response_model=list[DownloadCard])
    async def downloads_queue():
        """The cards of the downloads (core/contracts/downloads.py): one to a need of a plan, its stages and attempts."""
        try:
            reply = await gw.reply_of(DOWNLOADS_QUEUE_GET, {})
        except BusRequestError:
            return []
        return list(reply.tasks)

    @r.post("/api/downloads/queue/clear", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={500: {"model": ErrorResponse}})
    async def downloads_queue_clear():
        try:
            reply = await gw.reply_of(DOWNLOADS_QUEUE_CLEAR, {})
        except BusRequestError as exc:
            return JSONResponse({"success": False, "error": relayed(str(exc))},
                                status_code=500)
        return {"success": True,
                "message": reason("queue_tasks_removed", count=reply.deleted)}

    @r.get("/api/pack/queue", response_model=list[PackCard])
    async def pack_queue():
        """The cards of the packing (core/contracts/packing.py): every container a sync builds, its stages and tracks."""
        try:
            reply = await gw.reply_of(PLANNER_PACK_GET, {})
        except BusRequestError:
            return []
        return list(reply.tasks)

    @r.delete("/api/pack/cards/{card_id}", response_model=OkResponse,
              response_model_exclude_none=True,
              responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
    async def pack_card_remove(card_id: str):
        """A card of the packing that stayed after its sync is removed."""
        try:
            await gw.reply_of(PLANNER_PACK_REMOVE, {"id": card_id})
        except BusRequestError as exc:
            msg = str(exc)
            return JSONResponse({"success": False, "error": relayed(msg)},
                                status_code=404 if "LookupError" in msg else 500)
        return {"success": True}

    @r.delete("/api/downloads/cards/{card_id}", response_model=OkResponse,
              response_model_exclude_none=True,
              responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
    async def downloads_card_remove(card_id: str):
        """A card that stayed after its sync is removed."""
        try:
            await gw.reply_of(DOWNLOADS_CARD_REMOVE, {"id": card_id})
        except BusRequestError as exc:
            msg = str(exc)
            return JSONResponse({"success": False, "error": relayed(msg)},
                                status_code=404 if "LookupError" in msg else 500)
        return {"success": True}

    @r.post("/api/downloads/{task_id}/cancel", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={404: {"model": ErrorResponse},
                       500: {"model": ErrorResponse}})
    async def downloads_cancel(task_id: int):
        """A pinpoint cancel of a download: kill the process, remove the file, remove the task (finding 45)."""
        try:
            await gw.reply_of(DOWNLOADS_CANCEL, {"task_id": task_id})
        except BusRequestError as exc:
            msg = str(exc)
            code = 404 if "LookupError" in msg else 409 if msg.startswith("NotAllowed") else 500
            return JSONResponse(
                {"success": False, "error": relayed(msg)},
                status_code=code)
        return {"success": True, "message": reason("download_stopped_by_user")}

    @r.delete("/api/conform/jobs/{job_id}", response_model=ConformJobChange, responses={502: {"model": ErrorOnly}})
    async def conform_job_delete(job_id: str):
        """Stops a job that waits or runs; deletes the record of a job that ended."""
        try:
            reply = await gw.reply_of(CONFORM_CANCEL, {"job_id": job_id})
        except BusRequestError:
            reply = JobCancelled(done=False)
        if not reply.done:
            return JSONResponse({"error": reason("conform_job_unchanged")}, status_code=502)
        return {"done": True}

    @r.get("/api/conform/jobs/{job_id}/plot/{name}", response_class=Response,
           responses={200: {"content": {"image/png": {}}}, 404: {"model": ErrorOnly}, 502: {"model": ErrorOnly}})
    async def conform_plot(job_id: str, name: str):
        """The picture of an alignment plot, as the daemon made it."""
        try:
            reply = await gw.reply_of(CONFORM_PLOT, {"job_id": job_id, "name": name})
        except BusRequestError as exc:
            return JSONResponse({"error": relayed(str(exc))}, status_code=502)
        if not reply.found:
            return JSONResponse({"error": reason("conform_plot_missing")}, status_code=404)
        return Response(base64.b64decode(reply.data), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

    return r
