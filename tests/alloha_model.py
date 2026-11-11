"""A model of Alloha as its player meets it (modules/sources/players/alloha_session.py), for the tests and the stand: the
host of the player (the document of an iframe, the script with the key of the CDN, the answer of the files signed by
`Borth`, the reports), the socket that gives `edge_hash`, and the CDN that gives a playlist and its segments only with the
key and the latest `edge_hash`, else `403` with `x-vd: session_blocked`. Nothing here is a snapshot of a real answer.

`client(profile)` is the HTTP client a session is given: it answers from the model, never from the network. An episode
`served` from a folder (the stand: a real HLS of one height on its disk) is given by the CDN as that folder holds it.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from modules.sources.players.alloha_session import Profile, borth_tail

HOST = "https://alloha.yani.tv"
CDN = "https://cdn.alloha.test"
SOCKET = "wss://socket.alloha.test/ws"
SITE = "https://old.yummyani.me/"
KEY = "K" * 380
SEED = "0123456789abcdefghij"
PROFILE = Profile("f" * 64, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/142.0.0.0 Safari/537.36")


@dataclass
class Response:
    status_code: int
    content: bytes = b""
    headers: dict = field(default_factory=dict)
    url: str = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")


def document(voices: dict[str, dict[int, list[int]]], active: str = "91") -> str:
    """The document of an iframe: its seed of `Borth`, the list of the files of the title, the token of its reports and
    its script."""
    listed = {"type": "serial", "active": {"id": int(active)}, "all": {
        f"t{voice}": {"name": "Озвучка «А»", "file": {str(season): {
            str(episode): {"id": int(f"9{episode}"), "id_translation": int(voice), "seasons": season, "episode": episode}
            for episode in episodes} for season, episodes in seasons.items()}}
        for voice, seasons in voices.items()}}
    text = json.dumps(listed).replace("\\", "\\\\").replace("'", "\\'")
    return (f'<html><head><meta name="viewporti" content="{SEED}"></head><body>'
            f"<script>const userParam = {{token: 'view-token', domain: 'old.yummyani.me'}};</script>"
            f"<script>const playerConfigs = {{}}; \tconst fileList = JSON.parse('{text}');</script>"
            f'<script src="/build/app.5b27.js"></script></body></html>')


class Socket:
    """The socket of one session: what the player said, and the `edge_hash` it is given."""

    def __init__(self, model: "AllohaModel") -> None:
        self.model, self.said, self._inbox = model, [], asyncio.Queue()

    async def send_str(self, text: str) -> None:
        message = json.loads(text)
        self.said.append(message["type"])
        if message["type"] == "resumed":
            self.model.edges = ["E1"]
            await self._inbox.put(json.dumps({"type": "config_update", "edge_hash": "E1", "ttl": 60}))

    def give(self, edge: str) -> None:
        """The server gives a new `edge_hash`: the CDN takes it and the one before it (requests on their way), no older."""
        self.model.edges = [*self.model.edges[-1:], edge]
        self._inbox.put_nowait(json.dumps({"type": "config_update", "edge_hash": edge}))

    def drop(self) -> None:
        self._inbox.put_nowait(None)

    async def recv_str(self) -> str:
        item = await self._inbox.get()
        if item is None:
            raise ConnectionError("closed by the server")
        return item

    async def close(self, timeout: float = 2) -> None:
        self.model.closed += 1


class AllohaModel:
    def __init__(self, voices: dict[str, dict[int, list[int]]] | None = None, heights=(360, 720, 1080),
                 segments: int = 6) -> None:
        self.voices = voices if voices is not None else {"158": {1: [1, 2]}}
        self.heights, self.segments = heights, segments
        self.edges: list[str] = []  # the `edge_hash` values the CDN takes now
        self.asked: list[tuple[str, str]] = []  # (method, url)
        self.files_asked: list[dict] = []  # the headers of every ask of the files
        self.media: list[dict] = []  # the headers of every request of the CDN
        self.reports: list[str] = []
        self.sockets: list[Socket] = []
        self.closed = 0
        self.refuse_files: int | None = None  # a status the host answers every ask of the files with
        self.gone: set[int] = set()  # segments the CDN has not
        self.after_segment: dict[int, object] = {}  # an action when a segment is asked (renew the edge, drop the socket)
        self.served: dict[str, tuple[str, int]] = {}  # the number of the files of an episode -> (its HLS folder, height)

    def body(self, height: int, index: int) -> bytes:
        return f"[{height}:{index}]".encode() * 50

    # --- the client of a session --------------------------------------------------------------------------------------

    def client(self, profile: Profile) -> "Client":
        return Client(self, profile)

    def handle(self, method: str, url: str, headers: dict, data: dict | None) -> Response:
        self.asked.append((method, url))
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin == HOST and parts.path in ("/", "") and method == "GET":
            assert headers.get("Referer") == SITE
            named = {key: values[0] for key, values in parse_qs(parts.query).items()}
            episodes = self.voices.get(named.get("translation", ""), {}).get(int(named.get("season", 0)), [])
            if int(named.get("episode", 0)) not in episodes:
                return Response(404, b"<title>Error</title>", url=url)
            return Response(200, document(self.voices, f"9{named['episode']}").encode(), url=url)
        if origin == HOST and parts.path.startswith("/build/app."):
            return Response(200, f"var a='short';var k=\"{KEY}\";".encode(), url=url)
        if origin == HOST and parts.path.startswith("/bnsi/movies/"):
            self.files_asked.append(dict(headers))
            if self.refuse_files is not None:
                return Response(self.refuse_files, b"{}", url=url)
            if headers.get("Borth") != f"{PROFILE.fingerprint}|{borth_tail(SEED)}" or not data or not data.get("token"):
                return Response(403, b"{}", url=url)
            files = parts.path.rsplit("/", 1)[-1]
            heights = (self.served[files][1],) if files in self.served else self.heights
            return Response(200, json.dumps({
                "hlsSource": [{"label": "AniStar", "audioId": "a1", "default": True, "quality": {
                    str(height): f"{CDN}/{files}/{height}/master.m3u8 or {CDN}/mirror/{height}.m3u8"
                    for height in heights}}],
                "pnr": SOCKET + "?x=1", "pnk": "sid-" + files}).encode(), url=url)
        if origin == HOST and parts.path in ("/events", "/stat"):
            self.reports.append(parts.path)
            return Response(200, b"{}", url=url)
        if origin == CDN:
            self.media.append(dict(headers))
            if headers.get("Authorizations") != "Bearer " + KEY or headers.get("Accepts-Controls") not in self.edges:
                return Response(403, b"blocked", {"x-vd": "session_blocked"}, url=url)
            files, height, name = parts.path.strip("/").split("/")
            if files in self.served and name != "master.m3u8":
                return self._from_folder(self.served[files][0], name, url)
            if name == "master.m3u8":
                return Response(200, (f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n"
                                      f"#EXT-X-STREAM-INF:BANDWIDTH=900\nhigh.m3u8\n").encode(), url=url)
            if name == "high.m3u8":
                lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", '#EXT-X-MAP:URI="init.mp4"']
                for index in range(self.segments):
                    lines += ["#EXTINF:4.0,", f"seg{index}.m4s"]
                return Response(200, ("\n".join(lines + ["#EXT-X-ENDLIST"]) + "\n").encode(), url=url)
            if name == "init.mp4":
                return Response(200, b"INIT", {"Content-Type": "video/mp4"}, url=url)
            index = int(name.removeprefix("seg").removesuffix(".m4s"))
            if index in self.gone:
                return Response(404, b"", url=url)
            action = self.after_segment.pop(index, None)
            if action is not None:
                action()
            body = self.body(int(height), index)
            return Response(200, body, {"Content-Type": "video/iso.segment", "Content-Length": str(len(body))}, url=url)
        return Response(404, b"", url=url)


    def _from_folder(self, folder: str, name: str, url: str) -> Response:
        """A file of the HLS of an episode on disk: its playlist for the variant, a segment by its name."""
        index = int("".join(ch for ch in name if ch.isdigit()) or -1) if name != "high.m3u8" else -1
        if index in self.gone:
            return Response(404, b"", url=url)
        path = os.path.join(folder, "index.m3u8" if name == "high.m3u8" else os.path.basename(name))
        if not os.path.isfile(path):
            return Response(404, b"", url=url)
        with open(path, "rb") as file:
            body = file.read()
        kind = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
        return Response(200, body, {"Content-Type": kind, "Content-Length": str(len(body))}, url=url)


class Client:
    """The HTTP client of one session, as curl_cffi's AsyncSession is asked."""

    def __init__(self, model: AllohaModel, profile: Profile) -> None:
        self.model, self.profile = model, profile

    async def request(self, method: str, url: str, *, headers: dict, timeout: float, data: dict | None = None, **_kw):
        await asyncio.sleep(0)
        return self.model.handle(method, url, headers, data)

    async def ws_connect(self, url: str, *, headers: dict, timeout: float, **_kw) -> Socket:
        assert headers.get("Origin") == HOST and parse_qs(urlsplit(url).query).get("sid")
        socket = Socket(self.model)
        self.model.sockets.append(socket)
        return socket

    async def close(self) -> None:
        pass
