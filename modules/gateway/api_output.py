"""HTTP points of the output section of a series: its composition, the references and the levers of the output.

Each answer is the answer of one owner, as it is: the layout of `composition`, the references of `scan`, the
composition of a torrent from `torrents`. The window puts them side by side by (season, episode); the gateway
does not merge them.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from core import BusRequestError
from core.contracts import (CompositionGetRequest, CompositionReply, DubsScope, OutputSwitch, QueuesGet, QueuesReply, Reference,
                            SeriesKind, SettingsReset, SettingsSet, VoiceOrderSet)

from .routing import admitted, refusal
from .schemas import (DubsActionBody, DubsRestarted, DynamicObject, ErrorOnly, ErrorResponse, OkResponse,
                      OutputSettingsReset, OutputSettingsSet, OutputSwitchBody, RefetchBody, RefetchQueued, VoiceOrderBody)

_ERR = {code: {"model": ErrorResponse} for code in (400, 404, 500, 503)}
_ERR1 = {code: {"model": ErrorOnly} for code in (404, 500)}


def _iso(value):
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def _contract(model, **fields):
    """The command the owner reads, built from the body of the request: a body that breaks the contract is a
    malformed request (422), not a failure of the owner."""
    try:
        return model(**fields)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_context=False, include_input=False))


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    @r.get("/api/series/{series_id}/composition", response_model=CompositionReply | list[DynamicObject],
           responses=_ERR1)
    async def composition(series_id: int, refresh: bool = False, include_excluded: bool = False,
                          sources_only: bool = False):
        """The composition window: the layout of a video series (`composition`, rows the constructor excluded
        only with `include_excluded`; `sources_only` leaves out the rows, levels and settings), or the composition of
        a torrent series (`torrents`; `refresh` fetches
        the release first)."""
        try:
            series = await gw.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError:
            return JSONResponse({"error": "Сериал не найден"}, status_code=404)
        try:
            if series["kind"] == SeriesKind.VIDEO:
                return await gw.request("composition.get", CompositionGetRequest(
                    series_id=series_id, include_excluded=include_excluded,
                    sources_only=sources_only).model_dump(mode="json"))
            if refresh:
                # Fetch the release and split it into files without starting the download.
                await gw.request("scan.series.prepare", {"series_id": series_id})
            plan = await gw.request("torrents.composition", {"series_id": series_id, "refresh": refresh})
        except BusRequestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        for item in plan:
            sd = item.get("source_data")
            if sd:
                sd["publication_date"] = _iso(sd.get("publication_date"))
        return plan

    @r.get("/api/series/{series_id}/output", response_model=list[Reference], responses=_ERR1)
    async def output(series_id: int):
        """The reference of every episode of the series and why it is that one (the mark)."""
        try:
            return await gw.request("scan.output.get", {"series_id": series_id})
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.get("/api/series/{series_id}/output/queues", response_model=QueuesReply, responses=_ERR1)
    async def output_queues(series_id: int, season: int | None = None, episode: int | None = None):
        """The queue of the reference and of the voice-over of each group of a level (the series, a season, or an episode
        of a season), in the order the core tries their variants, with what the levers of the level say of each."""
        query = _contract(QueuesGet, series_id=series_id, season=season, episode=episode)
        try:
            return await gw.request("composition.queues.get", query.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    async def restart_dubs(series_id: int, body: DubsActionBody, topic: str):
        scope = _contract(DubsScope, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                done = await gw.request(topic, scope.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True, "restarted": int((done or {}).get("restarted") or 0)}

    @r.post("/api/series/{series_id}/output/dubs/retry", response_model=DubsRestarted, responses=_ERR)
    async def retry_dubs(series_id: int, body: DubsActionBody):
        """«Повторить»: the voice-overs of the scope that failed or were rejected start over and go to download now."""
        return await restart_dubs(series_id, body, "voices.dubs.retry")

    @r.post("/api/series/{series_id}/output/dubs/redo", response_model=DubsRestarted, responses=_ERR)
    async def redo_dubs(series_id: int, body: DubsActionBody):
        """«Пересоздать»: the voice-overs of the scope whose track is placed are made anew, now; the container of the
        episode takes the new tracks at the next sync."""
        return await restart_dubs(series_id, body, "voices.dubs.redo")

    @r.post("/api/series/{series_id}/output/reference/refetch", response_model=RefetchQueued, responses=_ERR)
    async def refetch_reference(series_id: int, body: RefetchBody):
        """«Скачать заново»: the reference video of the episode is downloaded again now; its file is replaced when the new
        one is whole, and the voice-overs of the episode are aligned to it again."""
        try:
            async with admitted(gw, series_id):
                done = await gw.request("downloads.reference.refetch", {"series_id": series_id, **body.model_dump()})
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True, "queued": bool((done or {}).get("queued"))}

    @r.put("/api/series/{series_id}/output/switch", response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERR)
    async def switch_output(series_id: int, body: OutputSwitchBody):
        """Switches an episode (or, without `episode`, a whole season) off or on. What the output shows of it comes
        as references: the episode gets the mark `off`, or its reference back."""
        command = _contract(OutputSwitch, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("scan.output.switch", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.put("/api/series/{series_id}/output/settings", response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERR)
    async def set_output_setting(series_id: int, body: OutputSettingsSet):
        """Sets the value of a lever at a level (the series, a season or an episode); a lever, a target or a value
        that the registry does not know is refused with its code (`unknown_lever`, `bad_target`, `bad_value`)."""
        command = _contract(SettingsSet, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.settings.set", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.put("/api/series/{series_id}/output/voice-order", response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERR)
    async def set_voice_order(series_id: int, body: VoiceOrderBody):
        """The user's order of the voice-overs of a season: the order of the tracks in its containers. A plan: the
        containers follow it at the next sync; refused while a sync of the series is under way."""
        command = _contract(VoiceOrderSet, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.voice_order.set", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/output/settings/reset", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def reset_output_settings(series_id: int, body: OutputSettingsReset):
        """Takes the levers of a level (or the one named) back to the level above."""
        command = _contract(SettingsReset, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.settings.reset", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    return r
