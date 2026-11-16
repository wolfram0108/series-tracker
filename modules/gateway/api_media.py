"""HTTP points of the operations of a series (Р-21): the reprocessing of names, the names of the source files, the preview
of the naming of the add form. The points of the old path of the cutting of compilations (media items, chapters, slicing,
deep adoption, the preview of renaming by media items) left with it (2026-11-17)."""
from __future__ import annotations

import os

from fastapi import APIRouter

from core import BusRequestError
from core.reasons import reason

from .routing import refusal
from .schemas import ErrorOnly, ErrorResponse, NamingPreviewAccepted, NamingPreviewRequest, OkResponse

# Документация веток ошибок (отдаются JSONResponse, response_model на них
# не распространяется). _ERR — тело {success, error}; _ERR1 — {error}.
_ERR = {c: {"model": ErrorResponse} for c in (400, 404, 409, 500)}
_ERR1 = {c: {"model": ErrorOnly} for c in (404, 500)}


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

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
