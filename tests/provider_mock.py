"""A provider of the socket (contracts/provider_socket.md) made by the specification alone, on real HTTP, for the tests of the
tracker's end of it. It holds applications with cards of rows a test gives it, tells its manifest and its changes on the feed,
answers a search, reads a card into frames, and gives the stream of a link — and what the test needs to watch: every request it
got, every read that began, every read that was cancelled by a closed connection. A test makes it slow, silent, broken, down.

Nothing here is a snapshot of a real provider: it is the specification played by a model."""
from __future__ import annotations

import asyncio
import json
import re
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.contracts import RowState, TableRow
from core.contracts.provider import (PROTOCOL, FeedEvent, Manifest, ManifestFamily, ManifestPlace, ManifestPlayer, ManifestSearch,
                                     ManifestSite, ProviderInfo, SiteAddress, StreamReply)

FAMILY = "line"


def link_of(app: str, card: int, line: str, item: str) -> str:
    return f"app://{app}/{card}#line={quote(line, safe='')}&item={quote(item, safe='')}"


@dataclass
class MockApp:
    name: str
    label: str
    cards: dict[int, dict] = field(default_factory=dict)  # card -> {names, title, rows: [TableRow dicts]}
    found: list[dict] = field(default_factory=list)


class MockProvider:
    def __init__(self, token: str = "", heartbeat_s: float = 0.2, *, protocol: int = PROTOCOL) -> None:
        self.token, self.heartbeat_s, self.protocol = token, heartbeat_s, protocol
        self.apps: dict[str, MockApp] = {}
        self.streams: dict[str, StreamReply] = {}
        self.requests: list[tuple[str, dict]] = []
        self.reads_begun = 0
        self.reads_cancelled = 0
        self.began = asyncio.Event()
        self.cancelled = asyncio.Event()
        # A read waits for this to be set when a test clears it (a slow read); a failure the test asks for is said as asked.
        self.release = asyncio.Event()
        self.release.set()
        self.read_error = ""
        self.read_breaks = False
        self.search_status = 200
        self.revision = 1
        self._watchers: set[asyncio.Event] = set()
        self._feeds_end: set[asyncio.Event] = set()

    # --- what a test gives the provider --------------------------------------------------------------------------

    def add_app(self, name: str, label: str) -> MockApp:
        app = self.apps[name] = MockApp(name, label)
        self.changed()
        return app

    def card(self, app: str, card: int, names: list[str], rows: list[tuple[str, str, str, list[int]]],
             title: str = "") -> None:
        """A card of an application: its rows are (text, line, item, heights); a row without a height is absent."""
        made = []
        for place, (text, line, item, heights) in enumerate(rows, start=1):
            made.append(TableRow(key=f"{app}:{line}:{place}", player=f"{FAMILY}:{line}", player_label=line, text=text,
                                 state=RowState.PLAYABLE if heights else RowState.ABSENT, heights=heights,
                                 link=link_of(app, card, line, item), site_fields={"app": app, "line": line}).model_dump(mode="json"))
        self.apps[app].cards[card] = {"names": names, "title": title or f"{self.apps[app].label} · {names[0]}", "rows": made}

    def stream_of(self, link: str, reply: StreamReply) -> None:
        self.streams[link] = reply

    def changed(self) -> None:
        """The manifest changed: a new revision, told on every open feed."""
        self.revision += 1
        for watcher in list(self._watchers):
            watcher.set()

    def drop_feeds(self) -> None:
        """End every open feed from this side, as a provider does that restarts."""
        for each in list(self._feeds_end):
            each.set()

    # --- what the provider tells ---------------------------------------------------------------------------------------

    def manifest(self) -> Manifest:
        sites = [ManifestSite(name=app.name, label=app.label, domains=[app.name], first_read_by_user=True,
                              address=SiteAddress(schemes=["app"], path=r"^/(?P<card>[0-9]+)/?$",
                                                  canonical=f"app://{app.name}/{{card}}"))
                 for app in self.apps.values()]
        hosts = "|".join(re.escape(name) for name in self.apps)
        families = [ManifestFamily(name=FAMILY, label="Линии приложений", video="video",
                                   owns=rf"^app://(?:{hosts})/[0-9]+#line=(?P<line>[^&]+)&item=(?P<video>[^&]+)$")] if self.apps else []
        lines: dict[str, list[ManifestPlace]] = {}
        for app in self.apps.values():
            for card in app.cards.values():
                for row in card["rows"]:
                    place = ManifestPlace(name=app.name, label=app.label)
                    held = lines.setdefault(row["site_fields"]["line"], [])
                    if place not in held:
                        held.append(place)
        players = [ManifestPlayer(name=f"{FAMILY}:{line}", label=line, kind="line", places=places)
                   for line, places in sorted(lines.items())]
        return Manifest(protocol=self.protocol, provider=ProviderInfo(id="mock", version="0"), revision=str(self.revision),
                        sites=sites, families=families, players=players,
                        search=[ManifestSearch(name=app.name, label=app.label) for app in self.apps.values()])

    def app(self) -> FastAPI:
        api = FastAPI()

        def guard(authorization: str) -> None:
            if self.token and authorization != f"Bearer {self.token}":
                raise HTTPException(status_code=401)

        @api.get("/v1/manifest")
        async def manifest(authorization: str = Header(default="")):
            guard(authorization)
            self.requests.append(("manifest", {}))
            return self.manifest()

        @api.get("/v1/events")
        async def events(authorization: str = Header(default="")):
            guard(authorization)

            async def feed() -> AsyncIterator[str]:
                watcher, end = asyncio.Event(), asyncio.Event()
                self._watchers.add(watcher)
                self._feeds_end.add(end)
                try:
                    sent = self.revision
                    yield f"event: hello\ndata: {json.dumps(FeedEvent(revision=str(sent)).model_dump())}\n\n"
                    while not end.is_set():
                        waits = {asyncio.ensure_future(watcher.wait()), asyncio.ensure_future(end.wait())}
                        try:
                            done, _pending = await asyncio.wait(waits, timeout=self.heartbeat_s,
                                                                return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for each in waits:
                                each.cancel()
                        if end.is_set():
                            return
                        if not done:
                            yield ": keep-alive\n\n"
                            continue
                        watcher.clear()
                        if self.revision != sent:
                            sent = self.revision
                            yield f"event: changed\ndata: {json.dumps(FeedEvent(revision=str(sent)).model_dump())}\n\n"
                finally:
                    self._watchers.discard(watcher)
                    self._feeds_end.discard(end)

            return StreamingResponse(feed(), media_type="text/event-stream")

        @api.post("/v1/search")
        async def search(request: Request, authorization: str = Header(default="")):
            guard(authorization)
            body = await request.json()
            self.requests.append(("search", body))
            if self.search_status != 200:
                raise HTTPException(status_code=self.search_status)
            asked = [app for app in self.apps.values() if not body.get("app") or app.name == body["app"]]
            return {"apps": [{"app": app.name, "label": app.label, "page": body.get("page", 1), "pages": 1,
                              "total": len(app.found), "cards": [
                                  {"app": app.name, "id": str(each["id"]), "url": f"app://{app.name}/{each['id']}",
                                   "name": each.get("name", "")} for each in app.found]} for app in asked]}

        @api.post("/v1/stream")
        async def stream(request: Request, authorization: str = Header(default="")):
            guard(authorization)
            body = await request.json()
            self.requests.append(("stream", body))
            reply = self.streams.get(body["url"])
            return reply if reply is not None else StreamReply(handled=False)

        @api.post("/v1/read")
        async def read(request: Request, authorization: str = Header(default="")):
            guard(authorization)
            body = await request.json()
            self.requests.append(("read", body))

            async def frames() -> AsyncIterator[str]:
                self.reads_begun += 1
                self.began.set()
                try:
                    yield json.dumps({"type": "progress", "phase": "release", "current": 0, "total": 1}) + "\n"
                    await self.release.wait()
                    yield json.dumps({"type": "progress", "phase": "release", "current": 1, "total": 1}) + "\n"
                    if self.read_error:
                        yield json.dumps({"type": "error", "reason": self.read_error}) + "\n"
                        return
                    if self.read_breaks:
                        return
                    match = re.fullmatch(r"app://([^/]+)/(\d+)", body["url"])
                    card = self.apps[match.group(1)].cards.get(int(match.group(2))) if match else None
                    if card is None:
                        yield json.dumps({"type": "error", "reason": json.dumps({"code": "app_title_absent"})}) + "\n"
                        return
                    yield json.dumps({"type": "draft", "draft": {"names": card["names"], "title": card["title"],
                                                                  "rows": card["rows"]}}, ensure_ascii=False) + "\n"
                except asyncio.CancelledError:
                    self.reads_cancelled += 1
                    self.cancelled.set()
                    raise

            return StreamingResponse(frames(), media_type="application/x-ndjson")

        return api


class MockServer:
    """The mock on a port: it goes down (the port refuses connections) and comes up on the same port, as a restarted provider."""

    def __init__(self, mock: MockProvider) -> None:
        self.mock = mock
        self.port = 0
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._sock: socket.socket | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def up(self) -> None:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(64)
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._server = uvicorn.Server(uvicorn.Config(self.mock.app(), log_level="warning", timeout_graceful_shutdown=1))
        self._task = asyncio.create_task(self._server.serve([sock]))

    async def down(self) -> None:
        if self._server is None:
            return
        self.mock.drop_feeds()
        self._server.should_exit = True
        await self._task
        self._sock.close()
        self._server = self._task = self._sock = None


@asynccontextmanager
async def serving(mock: MockProvider) -> AsyncIterator[MockServer]:
    server = MockServer(mock)
    await server.up()
    try:
        yield server
    finally:
        await server.down()
