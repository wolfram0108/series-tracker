"""HTTP-точки блока «скан и очереди» (Р-20) — формы routes/system.py.

/api/agent/reset не переносится: точка была сломана (находка 23) и
мертва на фронте, а «зависшие статусы» в новой системе невозможны —
статус нигде не хранится (Р-11).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import BusRequestError


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    @r.get("/api/scanner/status")
    async def scanner_status():
        return await gw.request("scan.status.get", {}, timeout=10)

    @r.post("/api/scanner/settings")
    async def scanner_settings(request: Request):
        data = await request.json()
        if "enabled" in data:
            await gw.request("settings.value.set", {
                "key": "scanner_agent_enabled",
                "value": str(data["enabled"]).lower()}, timeout=10)
        if "interval" in data:
            await gw.request("settings.value.set", {
                "key": "scan_interval_minutes",
                "value": str(data["interval"])}, timeout=10)
        # Пересчёт расписания делает scan по settings.changed (Р-20);
        # немедленный полный скан старой системы не воспроизводится.
        return {"success": True}

    @r.post("/api/scanner/scan_all")
    async def scan_all(request: Request):
        data = await request.json() if await request.body() else {}
        reply = await gw.request("scan.all.start", {
            "force_replace": data.get("debug_force_replace", False)},
            timeout=15)
        if not reply["started"]:
            return JSONResponse(
                {"success": False, "error": "Сканирование уже запущено."},
                status_code=409)
        return {"success": True,
                "message": "Сканирование всех сериалов запущено."}

    @r.get("/api/agent/queue")
    async def agent_queue():
        try:
            reply = await gw.request("torrents.queue.get", {}, timeout=10)
        except BusRequestError:
            return []  # модуль не поднят — как старый «нет агента»
        return [{"hash": t.get("torrent_hash"), **t} for t in reply["tasks"]]

    @r.get("/api/downloads/queue")
    async def downloads_queue():
        try:
            reply = await gw.request("downloads.queue.get", {}, timeout=10)
        except BusRequestError:
            return []
        return reply["tasks"]

    @r.post("/api/downloads/queue/clear")
    async def downloads_queue_clear():
        try:
            reply = await gw.request("downloads.queue.clear", {}, timeout=15)
        except BusRequestError as exc:
            return JSONResponse({"success": False, "error": str(exc)},
                                status_code=500)
        return {"success": True,
                "message": f"Удалено {reply['deleted']} задач из очереди."}

    return r
