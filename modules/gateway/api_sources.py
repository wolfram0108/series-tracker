"""HTTP points of the sources: the sources of a series, and the pages that are not a source yet (stage 10).

The gateway carries a request to the module that owns the answer and the answer back. It knows no kind of site
or player: what an address is, which table it has and what the constructor says of its rows are the answers of
`sources`, `composition` and `rules`.
"""
from __future__ import annotations

from fastapi import APIRouter

from core import BusRequestError
from core.contracts import (AppSearchReply, AppSearchRequest, CompositionPreviewReply, CompositionPreviewRequest,
                            SeriesSource, SourcesSettingsView, SiteResolveReply, SiteResolveRequest)

from .routing import admitted, refusal
from .schemas import (ChangedSource, CreatedSource, ErrorOnly, ErrorResponse, OkResponse, RulesTestRequest,
                      SourceAdd, SourceChange, TableReadRequest)

_ERR = {code: {"model": ErrorResponse} for code in (400, 404, 500, 503)}
_ERR1 = {code: {"model": ErrorOnly} for code in (400, 500, 503)}


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- the sources of a series --------------------------------------------------

    @r.get("/api/series/{series_id}/sources", response_model=list[SeriesSource], responses=_ERR)
    async def sources_of(series_id: int):
        try:
            return await gw.request("catalog.sources.list", {"series_id": series_id})
        except BusRequestError as exc:
            return refusal(exc)

    @r.post("/api/series/{series_id}/sources", response_model=CreatedSource, responses=_ERR)
    async def add_source(series_id: int, body: SourceAdd):
        try:
            async with admitted(gw, series_id):
                return await gw.request("catalog.sources.add", {
                    "series_id": series_id, "source": body.model_dump(mode="json", exclude_unset=True)})
        except BusRequestError as exc:
            return refusal(exc)

    @r.put("/api/series/{series_id}/sources/{source_id}", response_model=ChangedSource, responses=_ERR)
    async def change_source(series_id: int, source_id: int, body: SourceChange):
        # The catalog refuses a source of another series than the one the path names.
        try:
            async with admitted(gw, series_id):
                reply = await gw.request("catalog.sources.update", {
                    "series_id": series_id, "source_id": source_id,
                    "fields": body.model_dump(mode="json", exclude_unset=True)})
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True, "applied": reply["applied"]}

    @r.delete("/api/series/{series_id}/sources/{source_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERR)
    async def remove_source(series_id: int, source_id: int):
        try:
            async with admitted(gw, series_id):
                await gw.request("catalog.sources.remove", {"series_id": series_id, "source_id": source_id})
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    # --- an address that is no source of a series yet ------------------------------

    async def _preview(request: CompositionPreviewRequest):
        try:
            return await gw.request("composition.preview", request.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/sources/preview", response_model=CompositionPreviewReply, responses=_ERR1)
    async def preview_source(body: CompositionPreviewRequest):
        """What a window shows of an address before it is a source: the summary of its table and its lines."""
        return await _preview(body)

    @r.get("/api/sources/settings", response_model=SourcesSettingsView, responses=_ERR1)
    async def sources_settings():
        """The settings screen of the sources: the mirrors of each tracker, and the domains the service reads pages of
        now — the trackers with their mirrors, the sites of the registry."""
        try:
            return await gw.request("sources.settings.get", {})
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/sources/resolve", response_model=SiteResolveReply, responses=_ERR1)
    async def resolve_source(body: SiteResolveRequest):
        """Which site reads an address, and the address in its canonical form; no network."""
        try:
            return await gw.request("sources.site.resolve", body.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/sources/search", response_model=AppSearchReply, responses=_ERR1)
    async def search_apps(body: AppSearchRequest):
        """The search of the video applications: a page of the cards of each, as it gives them; a card is a source by its
        address."""
        try:
            return await gw.request("sources.apps.search", body.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/sources/read", response_model=OkResponse, responses=_ERR1)
    async def read_source(body: TableReadRequest):
        """Read the page now, whatever the age of its table (the hour of a table is the term of a sync, not of a user)."""
        gw.send_command("sources.table.read", body.model_dump(mode="json"))
        return {"success": True}

    @r.post("/api/parser-profiles/test", response_model=CompositionPreviewReply, responses=_ERR1)
    async def rules_test(body: RulesTestRequest):
        """The test of a profile: the same preview of a page, with the profile given, so that every line comes
        with the constructor's answer."""
        return await _preview(CompositionPreviewRequest(
            url=body.url, profile_id=body.profile_id, series_season=body.series_season,
            refresh=body.refresh, lines=body.lines))

    return r
