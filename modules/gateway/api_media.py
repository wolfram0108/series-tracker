"""HTTP-точки блока «media-items и операции серии» (Р-21).

Формы ответов — старые routes/media.py и часть routes/series.py.
Удалённые точки (Р-21): PUT /media-items/<int:id>/ignore (мёртвый дубль
uid-варианта), POST /series/<id>/reset_torrents (мертва, разрушительна),
POST /series/<id>/relocate (дубль сценария сохранения свойств, Р-19).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import BusRequestError


def _iso(value):
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def _media_error(exc: BusRequestError):
    """LookupError → 404, RuntimeError («уже…») → 409, ValueError → 400."""
    msg = str(exc)
    text = msg.split(": ", 1)[-1]
    if "LookupError" in msg:
        code = 404
    elif "RuntimeError" in msg and "уже" in msg:
        code = 409
    elif "ValueError" in msg:
        code = 400
    else:
        code = 500
    return JSONResponse({"success": False, "error": text}, status_code=code)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- media-items ------------------------------------------------------------

    @r.get("/api/series/{series_id}/media-items")
    async def media_items(series_id: int):
        items = await gw.request("scan.media.list",
                                 {"series_id": series_id}, timeout=15)
        for item in items:
            item["publication_date"] = _iso(item.get("publication_date"))
        return items

    @r.put("/api/media-items/{unique_id}/ignore")
    async def set_ignored(unique_id: str, request: Request):
        data = await request.json()
        is_ignored = data.get("is_ignored")
        if is_ignored is None:
            return JSONResponse({"success": False,
                                 "error": "Параметр is_ignored не указан"},
                                status_code=400)
        gw.send_command("scan.item.set_ignored", {
            "unique_id": unique_id, "is_ignored": bool(is_ignored)})
        return {"success": True}

    # --- главы и нарезка ----------------------------------------------------------

    @r.post("/api/media-items/{unique_id}/chapters")
    async def chapters(unique_id: str):
        try:
            return await gw.request("slicing.chapters.get",
                                    {"unique_id": unique_id}, timeout=300)
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse({"error": "Медиа-элемент не найден"},
                                    status_code=404)
            return JSONResponse(
                {"error": "Не удалось получить оглавление"}, status_code=500)

    @r.post("/api/media-items/{unique_id}/chapters/filtered")
    async def chapters_filtered(unique_id: str):
        try:
            reply = await gw.request("slicing.chapters.filtered",
                                     {"unique_id": unique_id}, timeout=300)
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse({"error": "Медиа-элемент не найден"},
                                    status_code=404)
            return JSONResponse(
                {"error": "Не удалось отфильтровать оглавление"},
                status_code=500)
        return _with_status_message(reply)

    @r.post("/api/media-items/{unique_id}/chapters/mark-garbage")
    async def chapters_mark(unique_id: str, request: Request):
        data = await request.json()
        indices = data.get("garbage_indices", [])
        if not isinstance(indices, list):
            return JSONResponse(
                {"error": "garbage_indices должен быть списком"},
                status_code=400)
        try:
            reply = await gw.request("slicing.chapters.mark", {
                "unique_id": unique_id, "garbage_indices": indices},
                timeout=60)
        except BusRequestError as exc:
            return _media_error(exc)
        return _with_status_message(reply)

    def _with_status_message(reply: dict) -> dict:
        if "expected_count" not in reply:
            return reply
        got = len(reply.get("filtered_chapters") or [])
        expected = reply["expected_count"]
        if got == expected:
            reply["status_message"] = (
                f"Количество отфильтрованных глав ({got}) совпало "
                "с ожидаемым.")
        else:
            reply["status_message"] = (
                f"Количество отфильтрованных глав ({got}) НЕ совпадает "
                f"с ожидаемым ({expected}).")
        return reply

    _SOURCE_MISSING_MSG = ("Исходник отсутствует, отправлен в загрузку. "
                           "Запустите нарезку повторно после завершения "
                           "загрузки.")

    @r.post("/api/media-items/{unique_id}/slice")
    async def slice_task(unique_id: str):
        try:
            reply = await gw.request("slicing.task.create",
                                     {"unique_id": unique_id}, timeout=60)
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("source_missing"):
            return {"success": False, "source_missing": True,
                    "message": _SOURCE_MISSING_MSG}
        return {"success": True,
                "message": "Задача на нарезку успешно создана."}

    @r.post("/api/media-items/{unique_id}/slice-with-filter")
    async def slice_with_filter(unique_id: str, request: Request):
        data = await request.json() if await request.body() else {}
        try:
            reply = await gw.request("slicing.task.create", {
                "unique_id": unique_id,
                "garbage_indices": data.get("garbage_indices", [])},
                timeout=60)
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("source_missing"):
            return {"success": False, "source_missing": True,
                    "message": _SOURCE_MISSING_MSG}
        return {"success": True,
                "message": "Задача на нарезку с фильтрацией успешно "
                           "создана.",
                "filtered_chapters_count": reply["chapters"]}

    @r.post("/api/media-items/{unique_id}/delete-source")
    async def delete_source(unique_id: str):
        try:
            reply = await gw.request("slicing.source.delete",
                                     {"unique_id": unique_id}, timeout=60)
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("deleted"):
            return {"success": True,
                    "message": "Исходный файл компиляции удалён."}
        return {"success": True, "deleted": False,
                "message": "Исходный файл уже отсутствует."}

    @r.post("/api/media-items/{unique_id}/verify-sliced-files")
    async def verify_sliced(unique_id: str):
        try:
            reply = await gw.request("slicing.verify",
                                     {"unique_id": unique_id}, timeout=120)
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse(
                    {"error": "Родительский медиа-элемент не найден"},
                    status_code=404)
            return JSONResponse({"error": str(exc)}, status_code=500)
        if reply["status"] == "none":
            reply["message"] = ("Записи о нарезанных файлах не найдены, "
                                "статус сброшен.")
        return reply

    @r.post("/api/series/{series_id}/deep-adoption")
    async def deep_adoption(series_id: int):
        gw.send_command("slicing.deep_adoption", {"series_id": series_id})
        return {"success": True,
                "message": "Процесс глубокого усыновления запущен "
                           "в фоновом режиме."}

    @r.get("/api/series/{series_id}/sliced-files")
    async def sliced_files(series_id: int):
        files = await gw.request("slicing.files.list",
                                 {"series_id": series_id}, timeout=15)
        items = await gw.request("scan.media.list",
                                 {"series_id": series_id}, timeout=15)
        parents = {i["unique_id"]: i for i in items}
        for f in files:
            parent = parents.get(f.get("source_media_item_unique_id"))
            if parent:
                f["parent_filename"] = (parent.get("final_filename")
                                        or "Источник не найден")
                f["season"] = parent.get("season") or 1
            else:
                f["parent_filename"] = "Источник не найден"
                f["season"] = 1
        return files

    # --- композиция и превью --------------------------------------------------------

    @r.get("/api/series/{series_id}/composition")
    async def composition(series_id: int, refresh: bool = False):
        try:
            series = await gw.request("catalog.series.get",
                                      {"series_id": series_id}, timeout=10)
        except BusRequestError:
            return JSONResponse({"error": "Сериал не найден"},
                                status_code=404)
        topic = ("scan.composition"
                 if series.get("source_type") == "vk_video"
                 else "torrents.composition")
        try:
            plan = await gw.request(topic, {
                "series_id": series_id, "refresh": refresh}, timeout=900)
        except BusRequestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        for item in plan:
            sd = item.get("source_data")
            if sd:
                sd["publication_date"] = _iso(sd.get("publication_date"))
        return plan

    @r.get("/api/series/{series_id}/rename_preview")
    async def rename_preview(series_id: int):
        try:
            return await gw.request("renaming.preview",
                                    {"series_id": series_id}, timeout=300)
        except BusRequestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    # --- переобработки ----------------------------------------------------------------

    async def _reprocess(series_id: int, message: str):
        active = await gw.request("renaming.tasks.active",
                                  {"series_id": series_id}, timeout=10)
        if active:
            return JSONResponse(
                {"success": False,
                 "error": "Задача на переобработку уже выполняется."},
                status_code=409)
        gw.send_command("renaming.reprocess", {"series_id": series_id})
        return {"success": True, "message": message}

    @r.post("/api/series/{series_id}/reprocess")
    async def reprocess(series_id: int):
        return await _reprocess(
            series_id, "Задача на переобработку файлов создана и "
                       "запущена в фоновом режиме.")

    @r.post("/api/series/{series_id}/reprocess_vk_files")
    async def reprocess_vk(series_id: int):
        return await _reprocess(
            series_id, "Задача на переобработку файлов VK-сериала "
                       "создана.")

    # --- имена для теста парсера ----------------------------------------------------

    @r.get("/api/series/{series_id}/source-filenames")
    async def source_filenames(series_id: int):
        try:
            series = await gw.request("catalog.series.get",
                                      {"series_id": series_id}, timeout=10)
        except BusRequestError:
            return []
        if series.get("source_type") == "torrent":
            try:
                files = await gw.request("torrents.db.files.for_series",
                                         {"series_id": series_id},
                                         timeout=10)
            except BusRequestError:
                return []
            names = [f["original_path"] for f in files]
        else:
            items = await gw.request("scan.media.list",
                                     {"series_id": series_id}, timeout=15)
            names = [i["final_filename"] for i in items
                     if i.get("final_filename")]
        return [os.path.basename(n) for n in names]

    return r
