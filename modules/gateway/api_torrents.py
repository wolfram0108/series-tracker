"""HTTP points of a torrent source (stage 15): the releases of its page, their download and update, and the torrent editor.

Each answer is its owner's, as it is: what the page lists now is `sources` (read from the tracker), the releases kept and
their life are `torrents`, the layout of the editor is `unpack`. The window («Торрент» of the window of a series) puts
them together; the gateway does not merge them.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from core.module import BusRequestError
from core.contracts import SubtitleType
from core.topics import SOURCES_RELEASES_GET

from .routing import as_made, refusal
from .schemas import DynamicObject, ErrorResponse, OkResponse

_ERR = {code: {"model": ErrorResponse} for code in (400, 404, 409, 500, 503)}


class PageBody(BaseModel):
    url: str


class ReleaseBody(BaseModel):
    url: str
    release: str


class WatchBody(ReleaseBody):
    watched: bool


class AssignBody(BaseModel):
    """Items of a tab placed by hand: a file in a season and an episode (to `episode_last` for a file of several), a track
    in a folder by its name. Nothing is left out here: what goes out of the output is the constructor's of the series."""
    url: str
    tab: Literal["files", "voices", "subtitles", "voice_streams", "subtitle_streams"]
    items: list[str] = Field(min_length=1)
    season: int | None = None
    episode: int | None = None
    episode_last: int | None = None
    # The version of the episode a file is (DIR): an episode with its tag is an episode of its own.
    tag: str = ""
    folder: str | None = None
    # The items in the order given take the episodes from `episode` on, one each (files dropped on a season).
    in_order: bool = False

    @model_validator(mode="after")
    def _a_place(self):
        if self.tab == "files" and (self.season is None or self.episode is None):
            raise ValueError("a file is placed in a season and an episode")
        if self.tab != "files" and self.tag.strip():
            raise ValueError("a tag is a video's: a track goes with the video of its episode")
        if self.tab != "files" and not self.folder:
            raise ValueError("a track is placed in a folder")
        return self


class UnassignBody(BaseModel):
    url: str
    tab: Literal["files", "voices", "subtitles", "voice_streams", "subtitle_streams"]
    items: list[str] = Field(min_length=1)


class FolderBody(BaseModel):
    url: str
    tab: Literal["voices", "subtitles", "voice_streams", "subtitle_streams"]
    name: str = Field(min_length=1)
    language: str = ""
    subtitle_type: SubtitleType = ""


class FolderRef(BaseModel):
    url: str
    tab: Literal["voices", "subtitles", "voice_streams", "subtitle_streams"]
    name: str = Field(min_length=1)
    # The folder holds tracks the automatic layout put there (the titles in the files, the rules): it is not made again
    # until «Авто». A folder only the hand filled or made goes and is not hidden.
    automatic: bool = False
    # The hand's placements in the folder: they go with it.
    items: list[str] = Field(default_factory=list)


class RemoveBody(BaseModel):
    """«−»: tracks taken out of their folders — red — until placed by hand or «Авто»."""
    url: str
    tab: Literal["voices", "subtitles", "voice_streams", "subtitle_streams"]
    items: list[str] = Field(min_length=1)


class AutoBody(BaseModel):
    """«Авто»: the automatic layout makes its deleted folders of these tabs again and places again the tracks taken out of
    folders; the hand's placements stay."""
    url: str
    tabs: list[Literal["voices", "subtitles", "voice_streams", "subtitle_streams"]] = Field(min_length=1)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    async def asked(topic: str, payload: dict):
        try:
            return await as_made(await gw.request(topic, payload))
        except BusRequestError as exc:
            return refusal(exc)

    async def told(topic: str, payload: dict):
        try:
            await gw.request(topic, payload)
        except BusRequestError as exc:
            return refusal(exc)
        return {"success": True}

    @r.post("/api/torrent/page", response_model=DynamicObject, responses=_ERR)
    async def page(body: PageBody):
        """What the page of the tracker lists now (read from the tracker): every release with its id on the page."""
        return await asked(SOURCES_RELEASES_GET, body.model_dump())

    @r.post("/api/torrent/releases", response_model=DynamicObject, responses=_ERR)
    async def releases(body: PageBody):
        """The releases kept for the page: state, version, folder, files."""
        return await asked("torrents.releases.get", body.model_dump())

    @r.post("/api/torrent/download", response_model=DynamicObject, responses=_ERR)
    async def download(body: ReleaseBody):
        """«Скачать»: the release goes to the client into its folder; a refusal says why (409)."""
        return await asked("torrents.release.download", body.model_dump())

    @r.post("/api/torrent/check", response_model=DynamicObject, responses=_ERR)
    async def check(body: PageBody):
        """The page is read now: a watched release it lists in another version begins its update, or waits for a sync."""
        return await asked("torrents.release.check", body.model_dump())

    @r.post("/api/torrent/watch", response_model=DynamicObject, responses=_ERR)
    async def watch(body: WatchBody):
        """The automatic update of the release on or off."""
        return await asked("torrents.release.watch", body.model_dump())

    @r.post("/api/torrent/editor", response_model=DynamicObject, responses=_ERR)
    async def editor(body: PageBody):
        """The torrent editor of the page: the items of its five tabs, the folders, what is unplaced, the profiles. The first
        opening of a page makes its profiles; the editor itself is a read."""
        try:
            await gw.request("unpack.editor.open", body.model_dump())
        except BusRequestError as exc:
            return refusal(exc)
        return await asked("unpack.editor.get", body.model_dump())

    @r.post("/api/torrent/editor/assign", response_model=OkResponse, responses=_ERR)
    async def assign(body: AssignBody):
        return await told("unpack.assign", body.model_dump())

    @r.post("/api/torrent/editor/unassign", response_model=OkResponse, responses=_ERR)
    async def unassign(body: UnassignBody):
        return await told("unpack.unassign", body.model_dump())

    @r.post("/api/torrent/editor/folder", response_model=OkResponse, responses=_ERR)
    async def folder_set(body: FolderBody):
        return await told("unpack.folder.set", body.model_dump())

    @r.post("/api/torrent/editor/folder/delete", response_model=OkResponse, responses=_ERR)
    async def folder_delete(body: FolderRef):
        return await told("unpack.folder.delete", body.model_dump())

    @r.post("/api/torrent/editor/remove", response_model=OkResponse, responses=_ERR)
    async def remove(body: RemoveBody):
        return await told("unpack.remove", body.model_dump())

    @r.post("/api/torrent/editor/auto", response_model=OkResponse, responses=_ERR)
    async def auto(body: AutoBody):
        return await told("unpack.auto", body.model_dump())

    return r
