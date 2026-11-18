"""A provider of the socket (contracts/provider_socket.md, protocol 2) made by the specification alone, on real HTTP, for the tests of
the tracker's end of it. It holds sites with titles of rows a test gives it, tells its manifest and its changes on the feed,
answers a search of titles, reads a title into frames, and downloads the file of a link into the path it is given — and what the test needs to watch: every request it
got, every read that began, every read or download that was cancelled by a closed connection. A test makes it slow, silent, broken, down.

Nothing here is a snapshot of a real provider: it is the specification played by a model."""
from __future__ import annotations

import asyncio
import json
import os
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
from core.contracts.provider import (PROTOCOL, AdoptItem, AdoptReply, FeedEvent, Manifest, ManifestFamily, ManifestPlace,
                                     ManifestPlayer, ManifestSearch, ManifestSite, ProviderInfo, SiteAddress)

FAMILY = "line"


def link_of(line: str, item: str) -> str:
    """The link of a row: the line and the token of the row, the token written in percent codes (the socket, §2.1)."""
    return f"line://{quote(line, safe='')}/{quote(item, safe='')}"


@dataclass
class MockSite:
    name: str
    label: str
    titles: dict[str, dict] = field(default_factory=dict)  # id -> {names, title, rows: [TableRow dicts]}
    found: list[dict] = field(default_factory=list)


class MockProvider:
    def __init__(self, token: str = "", heartbeat_s: float = 0.2, *, protocol: int = PROTOCOL) -> None:
        self.token, self.heartbeat_s, self.protocol = token, heartbeat_s, protocol
        self.sites: dict[str, MockSite] = {}
        # What a download of a link comes to: the frames it sends before its end and its end (a `done` or an `error` frame);
        # a link with no script is downloaded whole into the path, with a frame of telemetry before the end.
        self.downloads: dict[str, dict] = {}
        self.downloads_cancelled = 0
        self.download_begun = asyncio.Event()
        self.download_cancelled = asyncio.Event()
        self.download_release = asyncio.Event()
        self.download_release.set()
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
        # What the model adds to a page of its search beyond the titles: `more`, `without_lines` (contracts/provider_socket.md A.5).
        self.search_page: dict = {}
        # What the provider says of the addresses of protocol 1 it is asked to adopt: url -> AdoptItem dict.
        self.adoptions: dict[str, dict] = {}
        self.revision = 1
        self._watchers: set[asyncio.Event] = set()
        self._feeds_end: set[asyncio.Event] = set()

    # --- what a test gives the provider --------------------------------------------------------------------------

    def add_site(self, name: str, label: str) -> MockSite:
        site = self.sites[name] = MockSite(name, label)
        self.changed()
        return site

    def title(self, site: str, ident: str | int, names: list[str], rows: list[tuple[str, str, str, list[int]]],
              title: str = "") -> None:
        """A title of a site: its rows are (the episode as the door names it, line, token, heights); a row without a height is absent. The key of a row
        is `<line>:<place>`, its part the line (the socket, appendix A.2)."""
        made = []
        for place, (text, line, item, heights) in enumerate(rows, start=1):
            made.append(TableRow(key=f"{line}:{place}", player=f"{FAMILY}:{line}", player_label=line, episode=text, part=line,
                                 state=RowState.PLAYABLE if heights else RowState.ABSENT, heights=heights,
                                 link=link_of(line, item), site_fields={"line": line}).model_dump(mode="json"))
        self.sites[site].titles[str(ident)] = {"names": names, "title": title or f"{names[0]}", "rows": made}

    def download_of(self, link: str, end: dict) -> None:
        """How the download of a link ends: the frame (`done` with a height, or `error` with a reason and a kind)."""
        self.downloads[link] = end

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
        sites = [ManifestSite(name=site.name, label=site.label, domains=[site.name], first_read_by_user=True,
                              address=SiteAddress(schemes=["title"], path=r"^/(?P<title>[0-9a-z]{1,32})/(?P<line>[A-Za-z0-9_]+)/?$",
                                                  canonical=f"title://{site.name}/{{title}}/{{line}}"))
                 for site in self.sites.values()]
        families = [ManifestFamily(name=FAMILY, label="Линии", video="video",
                                   owns=r"^line://(?P<line>[A-Za-z0-9_]+)/(?P<video>[A-Za-z0-9._~%-]+)$")] if self.sites else []
        lines: dict[str, list[ManifestPlace]] = {}
        for site in self.sites.values():
            for held in site.titles.values():
                for row in held["rows"]:
                    place = ManifestPlace(name=site.name, label=site.label)
                    placed = lines.setdefault(row["site_fields"]["line"], [])
                    if place not in placed:
                        placed.append(place)
        players = [ManifestPlayer(name=f"{FAMILY}:{line}", label=line, kind="line", places=places)
                   for line, places in sorted(lines.items())]
        return Manifest(protocol=self.protocol, provider=ProviderInfo(id="mock", version="0"), revision=str(self.revision),
                        sites=sites, families=families, players=players,
                        search=ManifestSearch(label="mock") if self.sites else None)

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
            def lines_of(site: MockSite, each: dict) -> list[dict]:
                held = site.titles.get(str(each["id"]))
                codes = list(dict.fromkeys(row["site_fields"]["line"] for row in held["rows"])) if held else each.get("lines", [])
                return [{"code": code, "url": f"title://{site.name}/{each['id']}/{code}", "label": code, "routes": 3} for code in codes]

            titles = [{"id": str(each["id"]), "name": each.get("name", ""), "lines": lines_of(site, each)}
                      for site in self.sites.values() for each in site.found]
            return {"titles": titles, "page": body.get("page", 1), "pages": 1, "total": len(titles), **self.search_page}

        @api.post("/v1/adopt")
        async def adopt(request: Request, authorization: str = Header(default="")):
            guard(authorization)
            body = await request.json()
            self.requests.append(("adopt", body))
            return AdoptReply(items=[AdoptItem.model_validate(self.adoptions.get(url) or {
                "url": url, "reason": json.dumps({"code": "not_a_link_of_the_provider"})})
                for url in body.get("urls", [])]).model_dump(mode="json")

        @api.post("/v1/download")
        async def download(request: Request, authorization: str = Header(default="")):
            guard(authorization)
            body = await request.json()
            self.requests.append(("download", body))

            async def frames() -> AsyncIterator[str]:
                self.download_begun.set()
                try:
                    yield json.dumps({"type": "progress", "figures": {"phase": "link", "source_seconds": 1420.0}}) + "\n"
                    yield json.dumps({"type": "progress", "figures": {"phase": "download", "method": "hls", "progress": 40,
                                                                      "dlspeed": 3 * 1024 ** 2, "total_size_mb": 300.0}}) + "\n"
                    await self.download_release.wait()
                    end = self.downloads.get(body["url"])
                    if end is None:
                        os.makedirs(os.path.dirname(body["path"]), exist_ok=True)
                        with open(body["path"], "wb") as file:
                            file.write(b"the whole file")
                        end = {"type": "done", "height": 720}
                    yield json.dumps(end) + "\n"
                except asyncio.CancelledError:
                    self.downloads_cancelled += 1
                    self.download_cancelled.set()
                    raise

            return StreamingResponse(frames(), media_type="application/x-ndjson")

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
                    match = re.fullmatch(r"title://([^/]+)/([0-9a-z]+)/([A-Za-z0-9_]+)/?", body["url"])
                    card = self.sites[match.group(1)].titles.get(match.group(2)) if match and match.group(1) in self.sites else None
                    if card is None:
                        yield json.dumps({"type": "error", "reason": json.dumps({"code": "title_unknown"})}) + "\n"
                        return
                    rows = [row for row in card["rows"] if row["site_fields"]["line"] == match.group(3)]
                    if not rows:
                        yield json.dumps({"type": "error", "reason": json.dumps({"code": "title_no_lines", "line": match.group(3)})}) + "\n"
                        return
                    yield json.dumps({"type": "draft", "draft": {"names": card["names"], "title": card["title"],
                                                                  "rows": rows}}, ensure_ascii=False) + "\n"
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
