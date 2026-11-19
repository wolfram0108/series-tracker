"""HTTP points of the output section of a series: its composition, the references and the levers of the output.

Each answer is the answer of one owner, as it is: the layout of `composition`, the references of `scan`, the
composition of a torrent from `torrents`. The window puts them side by side by (season, episode); the gateway
does not merge them.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from typing import Literal

from pydantic import BaseModel, ValidationError

from core import BusRequestError
from core.reasons import reason, relayed
from core.contracts import (CompositionGetRequest, CompositionReply, LayoutPart, OutputSwitch, PlanDelete, PlannerView,
                            PlanRowRef, QueuesGet, QueuesReply, Reference, SettingsReset, SettingsSet, SettingsSetMany,
                            VoiceOrderSet, VoiceTitleSet)

from .routing import admitted, as_made, refusal
from .schemas import (ErrorOnly, ErrorResponse, OkResponse, OutputSettingsBatch, OutputSettingsReset,
                      OutputSettingsSet, OutputSwitchBody, VoiceOrderBody, VoiceTitleBody)

_ERR = {code: {"model": ErrorResponse} for code in (400, 404, 500, 503)}
_ERR1 = {code: {"model": ErrorOnly} for code in (404, 500)}


def _contract(model, **fields):
    """The command the owner reads, built from the body of the request: a body that breaks the contract is a
    malformed request (422), not a failure of the owner."""
    try:
        return model(**fields)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_context=False, include_input=False))


class PlanDeleteBody(BaseModel):
    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    @r.get("/api/series/{series_id}/composition", response_model=CompositionReply, responses=_ERR1)
    async def composition(series_id: int, include_excluded: bool = False, sources_only: bool = False,
                          part: LayoutPart = "whole", episodes: list[str] = Query(default=[])):
        """The composition window: the layout of the series (`composition`, rows the constructor excluded only with
        `include_excluded`; `sources_only` leaves out the rows, levels and settings). `part` is what of it is asked
        (`LayoutPart`: a window reads again only what a change touched), `episodes` — `season:episode` of the part
        `levers`."""
        named = [dict(zip(("season", "episode"), each.partition(":")[::2])) for each in episodes]
        query = _contract(CompositionGetRequest, series_id=series_id, include_excluded=include_excluded,
                          sources_only=sources_only, part=part, episodes=named)
        try:
            await gw.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError:
            return JSONResponse({"error": reason("series_not_found")}, status_code=404)
        try:
            return await as_made(await gw.request("composition.get", query.model_dump(mode="json")))
        except BusRequestError as exc:
            return JSONResponse({"error": relayed(str(exc))}, status_code=500)

    @r.get("/api/series/{series_id}/output", response_model=list[Reference], responses=_ERR1)
    async def output(series_id: int):
        """The reference of every episode of the series and why it is that one (the mark)."""
        try:
            # The reference a video series has is the one its plan takes (modules/planner).
            return await as_made(await gw.request("planner.references.get", {"series_id": series_id}))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.get("/api/series/{series_id}/output/queues", response_model=QueuesReply, responses=_ERR1)
    async def output_queues(series_id: int, season: int | None = None, episode: int | None = None,
                            compilation: str | None = None):
        """The queue of the reference and of the voice-over of each group of a level (the series, a season, a compilation
        of a season, or an episode of a season), in the order the core tries their variants, with what the levers of the
        level say of each."""
        query = _contract(QueuesGet, series_id=series_id, season=season, episode=episode, compilation=compilation)
        try:
            return await as_made(await gw.request("composition.queues.get", query.model_dump(mode="json")))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.get("/api/series/{series_id}/plan", response_model=PlannerView, responses=_ERR1)
    async def plan(series_id: int):
        """The plan of a video series (modules/planner): the asset with the provenance of every file and whether its row is
        online, the goal of every episode as the last sync counted it, the failures and the commands «Удалить» that wait."""
        try:
            return await as_made(await gw.request("planner.series.get", {"series_id": series_id}))
        except BusRequestError as exc:
            return refusal(exc, with_success=False)

    @r.post("/api/series/{series_id}/plan/delete", response_model=OkResponse, responses=_ERR)
    async def plan_delete(series_id: int, body: PlanDeleteBody):
        """«Удалить» of a file or of a track in it: carried out by the next sync. A video goes only for another video of its
        episode; with none the file stays and the command waits."""
        command = _contract(PlanDelete, series_id=series_id, **body.model_dump())
        try:
            await gw.request("planner.delete", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/plan/delete/cancel", response_model=OkResponse, responses=_ERR)
    async def plan_delete_cancel(series_id: int, body: PlanDeleteBody):
        """The command «Удалить» that waits is taken back."""
        command = _contract(PlanDelete, series_id=series_id, **body.model_dump())
        try:
            await gw.request("planner.delete.cancel", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

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

    @r.put("/api/series/{series_id}/output/settings/batch", response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERR)
    async def set_output_settings_batch(series_id: int, body: OutputSettingsBatch):
        """Sets several levers in one write: the boxes the user ticked, or all of a queue's off. One count of the plan
        follows for all of them; one item the registry refuses refuses all, with its code, and nothing is stored."""
        command = _contract(SettingsSetMany, series_id=series_id,
                            items=[{**item.model_dump(), "series_id": series_id} for item in body.items])
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.settings.set_many", command.model_dump(mode="json"))
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

    @r.put("/api/series/{series_id}/output/voice-title", response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERR)
    async def set_voice_title(series_id: int, body: VoiceTitleBody):
        """The user's name of the track of a voice-over group in the containers of the series. A plan: the containers
        take it at the next sync; refused while a sync of the series is under way."""
        command = _contract(VoiceTitleSet, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.voice_title.set", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/output/settings/reset", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def reset_output_settings(series_id: int, body: OutputSettingsReset):
        """Takes the levers of a level (or the one named, or those of the queue named) back to the level above."""
        command = _contract(SettingsReset, series_id=series_id, **body.model_dump())
        try:
            async with admitted(gw, series_id):
                await gw.request("composition.settings.reset", command.model_dump(mode="json"))
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    return r
