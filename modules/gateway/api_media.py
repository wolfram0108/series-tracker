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
from core.reasons import code_of, reason, relayed

from .routing import admitted, refusal

from .schemas import (DynamicObject, ErrorOnly, ErrorResponse, NamingPreviewAccepted, NamingPreviewRequest,
                      OkResponse)

# Документация веток ошибок (отдаются JSONResponse, response_model на них
# не распространяется). _ERR — тело {success, error}; _ERR1 — {error}.
_ERR = {c: {"model": ErrorResponse} for c in (400, 404, 409, 500)}
_ERR1 = {c: {"model": ErrorOnly} for c in (404, 500)}


def _iso(value):
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def _media_error(exc: BusRequestError):
    """LookupError → 404, RuntimeError («уже…») → 409, ValueError → 400."""
    msg = str(exc)
    text = relayed(msg)
    if "LookupError" in msg:
        code = 404
    elif code_of(msg) == "slicing_already_queued":
        code = 409
    elif "ValueError" in msg:
        code = 400
    else:
        code = 500
    return JSONResponse({"success": False, "error": text}, status_code=code)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- media-items ------------------------------------------------------------

    @r.get("/api/series/{series_id}/media-items",
           response_model=list[DynamicObject])
    async def media_items(series_id: int):
        items = await gw.request("scan.media.list",
                                 {"series_id": series_id})
        for item in items:
            item["publication_date"] = _iso(item.get("publication_date"))
        return items

    # --- главы и нарезка ----------------------------------------------------------

    @r.post("/api/media-items/{unique_id}/chapters",
            response_model=list[DynamicObject], responses=_ERR1)
    async def chapters(unique_id: str):
        try:
            return await gw.request("slicing.chapters.get",
                                    {"unique_id": unique_id})
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse({"error": reason("media_not_found")},
                                    status_code=404)
            return JSONResponse(
                {"error": reason("chapters_failed")}, status_code=500)

    @r.post("/api/media-items/{unique_id}/chapters/filtered",
            response_model=DynamicObject, responses=_ERR1)
    async def chapters_filtered(unique_id: str):
        try:
            reply = await gw.request("slicing.chapters.filtered",
                                     {"unique_id": unique_id})
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse({"error": reason("media_not_found")},
                                    status_code=404)
            return JSONResponse(
                {"error": reason("chapters_filter_failed")},
                status_code=500)
        return _with_status_message(reply)

    @r.post("/api/media-items/{unique_id}/chapters/mark-garbage",
            response_model=DynamicObject, responses=_ERR)
    async def chapters_mark(unique_id: str, request: Request):
        data = await request.json()
        indices = data.get("garbage_indices", [])
        if not isinstance(indices, list):
            return JSONResponse(
                {"error": reason("garbage_not_list")},
                status_code=400)
        try:
            reply = await gw.request("slicing.chapters.mark", {
                "unique_id": unique_id, "garbage_indices": indices})
        except BusRequestError as exc:
            return _media_error(exc)
        return _with_status_message(reply)

    def _with_status_message(reply: dict) -> dict:
        if "expected_count" not in reply:
            return reply
        got = len(reply.get("filtered_chapters") or [])
        expected = reply["expected_count"]
        if got == expected:
            reply["status_message"] = reason("chapters_count_match", got=got)
        else:
            reply["status_message"] = reason("chapters_count_differs", got=got, expected=expected)
        return reply

    _SOURCE_MISSING_MSG = reason("slicing_source_sent_to_download")

    @r.post("/api/media-items/{unique_id}/slice", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def slice_task(unique_id: str):
        try:
            reply = await gw.request("slicing.task.create",
                                     {"unique_id": unique_id})
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("source_missing"):
            return {"success": False, "source_missing": True,
                    "message": _SOURCE_MISSING_MSG}
        return {"success": True,
                "message": reason("slicing_task_created")}

    @r.post("/api/media-items/{unique_id}/slice-with-filter",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERR)
    async def slice_with_filter(unique_id: str, request: Request):
        data = await request.json() if await request.body() else {}
        try:
            reply = await gw.request("slicing.task.create", {
                "unique_id": unique_id,
                "garbage_indices": data.get("garbage_indices", [])})
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("source_missing"):
            return {"success": False, "source_missing": True,
                    "message": _SOURCE_MISSING_MSG}
        return {"success": True,
                "message": reason("slicing_task_created_filtered"),
                "filtered_chapters_count": reply["chapters"]}

    @r.post("/api/media-items/{unique_id}/delete-source",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERR)
    async def delete_source(unique_id: str):
        try:
            reply = await gw.request("slicing.source.delete",
                                     {"unique_id": unique_id})
        except BusRequestError as exc:
            return _media_error(exc)
        if reply.get("deleted"):
            return {"success": True,
                    "message": reason("slicing_source_deleted")}
        return {"success": True, "deleted": False,
                "message": reason("slicing_source_already_gone")}

    @r.post("/api/media-items/{unique_id}/verify-sliced-files",
            response_model=DynamicObject, responses=_ERR1)
    async def verify_sliced(unique_id: str):
        try:
            reply = await gw.request("slicing.verify",
                                     {"unique_id": unique_id})
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return JSONResponse(
                    {"error": reason("parent_media_not_found")},
                    status_code=404)
            return JSONResponse({"error": relayed(str(exc))}, status_code=500)
        if reply["status"] == "none":
            reply["message"] = reason("sliced_records_missing")
        return reply

    @r.post("/api/series/{series_id}/deep-adoption", response_model=OkResponse,
            response_model_exclude_none=True)
    async def deep_adoption(series_id: int):
        """The compilations whose children all lie on the disk are adopted into the plan: a change of the series,
        admitted like any other and answered when it is written."""
        try:
            async with admitted(gw, series_id):
                reply = await gw.request("slicing.deep_adoption", {"series_id": series_id})
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True,
                "message": reason("compilations_adopted", count=reply.get("adopted", 0))}

    # --- rename preview ---------------------------------------------------------------

    @r.get("/api/series/{series_id}/rename_preview",
           response_model=DynamicObject, responses=_ERR1)
    async def rename_preview(series_id: int):
        try:
            return await gw.request("renaming.preview",
                                    {"series_id": series_id})
        except BusRequestError as exc:
            return JSONResponse({"error": relayed(str(exc))}, status_code=500)

    # --- переобработки ----------------------------------------------------------------

    @r.post("/api/series/{series_id}/reprocess", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def reprocess(series_id: int):
        """The files take the names of the rules: the disk changes in a sync alone (И-59), so this is a sync of the
        series that fetches nothing; refused while one runs."""
        try:
            await gw.request("scan.series.run", {"series_id": series_id, "download": False})
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True,
                "message": reason("files_follow_rules")}

    # --- имена для теста парсера ----------------------------------------------------

    @r.get("/api/series/{series_id}/source-filenames",
           response_model=list[str])
    async def source_filenames(series_id: int):
        """File names of the series' torrents for the rules test; a video series has none, its lines
        for the test come from composition.preview."""
        try:
            files = await gw.request("torrents.db.files.for_series",
                                     {"series_id": series_id})
        except BusRequestError:
            return []
        return [os.path.basename(f["original_path"]) for f in files]

    # --- the file lists of releases for the naming preview of the add window ---------

    @r.post("/api/preview/naming/fetch", response_model=NamingPreviewAccepted, responses=_ERR1)
    async def preview_naming_fetch(body: NamingPreviewRequest):
        """Starts the fetch of the file lists of the releases and answers at once with {accepted: [torrent_id]};
        the lists come as the events preview_naming_ready and preview_naming_failed, published by the owner."""
        gw.send_command("torrents.preview.fetch", {"items": body.model_dump(mode="json", exclude_unset=True)["items"]})
        return {"accepted": [item.torrent_id for item in body.items]}

    return r
