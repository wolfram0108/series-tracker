"""HTTP points of the sources: the sources of a series, and the pages that are not a source yet (stage 10).

The gateway carries a request to the module that owns the answer and the answer back. It knows no kind of site
or player: what an address is, which table it has and what the constructor says of its rows are the answers of
`sources`, `composition` and `rules`.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core import BusRequestError
from core.contracts import (CompositionPreviewReply, CompositionPreviewRequest, SeriesSource, SiteResolveReply,
                            SiteResolveRequest)
from core.reasons import reason

from .routing import refusal
from .schemas import (ChangedSource, CreatedSource, ErrorOnly, ErrorResponse, OkResponse, RulesTestRequest,
                      SourceAdd, SourceChange)

_ERR = {code: {"model": ErrorResponse} for code in (400, 404, 500, 503)}
_ERR1 = {code: {"model": ErrorOnly} for code in (400, 500, 503)}

# A safeguard, not a sync: the constructor answers every line of a page, up to 300 s in `composition`.
_PREVIEW_TIMEOUT_S = 330.0


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- the sources of a series --------------------------------------------------

    @r.get("/api/series/{series_id}/sources", response_model=list[SeriesSource], responses=_ERR)
    async def sources_of(series_id: int):
        try:
            return await gw.request("catalog.sources.list", {"series_id": series_id}, timeout=10)
        except BusRequestError as exc:
            return refusal(exc)

    @r.post("/api/series/{series_id}/sources", response_model=CreatedSource, responses=_ERR)
    async def add_source(series_id: int, body: SourceAdd):
        try:
            return await gw.request("catalog.sources.add", {
                "series_id": series_id, "source": body.model_dump(mode="json", exclude_unset=True)}, timeout=10)
        except BusRequestError as exc:
            return refusal(exc)

    async def _own_source(series_id: int, source_id: int) -> JSONResponse | None:
        """The path names a series and one of its sources; a source of another series is not found under this one."""
        try:
            own = await gw.request("catalog.sources.list", {"series_id": series_id}, timeout=10)
        except BusRequestError as exc:
            return refusal(exc)
        if any(each["id"] == source_id for each in own):
            return None
        return JSONResponse({"success": False, "error": reason("source_not_found", source_id=source_id)},
                            status_code=404)

    @r.put("/api/series/{series_id}/sources/{source_id}", response_model=ChangedSource, responses=_ERR)
    async def change_source(series_id: int, source_id: int, body: SourceChange):
        if (missing := await _own_source(series_id, source_id)) is not None:
            return missing
        try:
            reply = await gw.request("catalog.sources.update", {
                "source_id": source_id, "fields": body.model_dump(mode="json", exclude_unset=True)}, timeout=10)
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True, "applied": reply["applied"]}

    @r.delete("/api/series/{series_id}/sources/{source_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERR)
    async def remove_source(series_id: int, source_id: int):
        if (missing := await _own_source(series_id, source_id)) is not None:
            return missing
        try:
            await gw.request("catalog.sources.remove", {"source_id": source_id}, timeout=10)
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    # --- an address that is no source of a series yet ------------------------------

    async def _preview(request: CompositionPreviewRequest):
        try:
            return await gw.request("composition.preview", request.model_dump(mode="json"),
                                    timeout=_PREVIEW_TIMEOUT_S)
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/sources/preview", response_model=CompositionPreviewReply, responses=_ERR1)
    async def preview_source(body: CompositionPreviewRequest):
        """What a window shows of an address before it is a source: the summary of its table and its lines."""
        return await _preview(body)

    @r.post("/api/sources/resolve", response_model=SiteResolveReply, responses=_ERR1)
    async def resolve_source(body: SiteResolveRequest):
        """Which site reads an address, and the address in its canonical form; no network."""
        try:
            return await gw.request("sources.site.resolve", body.model_dump(mode="json"), timeout=10)
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/parser-profiles/test", response_model=CompositionPreviewReply, responses=_ERR1)
    async def rules_test(body: RulesTestRequest):
        """The test of a profile: the same preview of a page, with the profile given, so that every line comes
        with the constructor's answer."""
        return await _preview(CompositionPreviewRequest(
            url=body.url, options=body.options, profile_id=body.profile_id, series_season=body.series_season))

    return r
