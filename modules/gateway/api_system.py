"""HTTP-точки блока «скан и очереди» (Р-20) — формы routes/system.py.

/api/agent/reset не переносится: точка была сломана (находка 23) и
мертва на фронте, а «зависшие статусы» в новой системе невозможны —
статус нигде не хранится (Р-11).
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core import BusRequestError
from core.reasons import reason

from .schemas import ConformJobChange, ErrorOnly, ErrorResponse, OkResponse, QueueTask, ScannerStatus


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    @r.get("/api/scanner/status", response_model=ScannerStatus)
    async def scanner_status():
        return await gw.request("scan.status.get", {})

    @r.post("/api/scanner/settings", response_model=OkResponse,
            response_model_exclude_none=True)
    async def scanner_settings(request: Request):
        data = await request.json()
        if "enabled" in data:
            await gw.request("settings.value.set", {
                "key": "scanner_agent_enabled",
                "value": str(data["enabled"]).lower()})
        if "interval" in data:
            await gw.request("settings.value.set", {
                "key": "scan_interval_minutes",
                "value": str(data["interval"])})
        # Пересчёт расписания делает scan по settings.changed (Р-20);
        # немедленный полный скан старой системы не воспроизводится.
        return {"success": True}

    @r.post("/api/scanner/scan_all", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={409: {"model": ErrorResponse}})
    async def scan_all(request: Request):
        data = await request.json() if await request.body() else {}
        reply = await gw.request("scan.all.start", {
            "force_replace": data.get("debug_force_replace", False)})
        if not reply["started"]:
            return JSONResponse(
                {"success": False, "error": "Сканирование уже запущено."},
                status_code=409)
        return {"success": True,
                "message": "Сканирование всех сериалов запущено."}

    @r.get("/api/agent/queue", response_model=list[QueueTask])
    async def agent_queue():
        try:
            reply = await gw.request("torrents.queue.get", {})
        except BusRequestError:
            return []  # модуль не поднят — как старый «нет агента»
        return [{"hash": t.get("torrent_hash"), **t} for t in reply["tasks"]]

    @r.get("/api/downloads/queue", response_model=list[QueueTask])
    async def downloads_queue():
        try:
            reply = await gw.request("downloads.queue.get", {})
        except BusRequestError:
            return []
        return reply["tasks"]

    @r.post("/api/downloads/queue/clear", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={500: {"model": ErrorResponse}})
    async def downloads_queue_clear():
        try:
            reply = await gw.request("downloads.queue.clear", {})
        except BusRequestError as exc:
            return JSONResponse({"success": False, "error": str(exc)},
                                status_code=500)
        return {"success": True,
                "message": f"Удалено {reply['deleted']} задач из очереди."}

    @r.post("/api/downloads/{task_id}/cancel", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={404: {"model": ErrorResponse},
                       500: {"model": ErrorResponse}})
    async def downloads_cancel(task_id: int):
        """Точечная отмена загрузки: убить процесс + удалить файл +
        снять задачу (находка 45)."""
        try:
            await gw.request("downloads.cancel", {"task_id": task_id})
        except BusRequestError as exc:
            msg = str(exc)
            code = 404 if "LookupError" in msg else 409 if msg.startswith("NotAllowed") else 500
            return JSONResponse(
                {"success": False, "error": msg.split(": ", 1)[-1]},
                status_code=code)
        return {"success": True, "message": "Загрузка отменена."}

    @r.delete("/api/conform/jobs/{job_id}", response_model=ConformJobChange, responses={502: {"model": ErrorOnly}})
    async def conform_job_delete(job_id: str):
        """Stops a job that waits or runs; deletes the record of a job that ended."""
        try:
            reply = await gw.request("conform.cancel", {"job_id": job_id})
        except BusRequestError:
            reply = {"done": False}
        if not reply["done"]:
            return JSONResponse({"error": reason("conform_job_unchanged")}, status_code=502)
        return {"done": True}

    @r.get("/api/conform/jobs/{job_id}/plot/{name}", response_class=Response,
           responses={200: {"content": {"image/png": {}}}, 404: {"model": ErrorOnly}, 502: {"model": ErrorOnly}})
    async def conform_plot(job_id: str, name: str):
        """The picture of an alignment plot, as the daemon made it."""
        try:
            reply = await gw.request("conform.plot", {"job_id": job_id, "name": name})
        except BusRequestError as exc:
            return JSONResponse({"error": str(exc).split(": ", 1)[-1]}, status_code=502)
        if not reply["found"]:
            return JSONResponse({"error": reason("conform_plot_missing")}, status_code=404)
        return Response(base64.b64decode(reply["data"]), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

    return r
