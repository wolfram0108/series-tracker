"""The stand of the whole application: the process as the service is assembled (assembly.py), every module real, on a
database made by the migrations; what is outside the service is a model.

- A site of the form of ruchime.org (the release page with its list of episodes and its voice-over, the page of every
  episode with the button of its player) and the AllVideo player, served to the real parser and the real player over the
  in-process web of `sources` (tests/sources_support.Web). The file the player names lies on a host on real HTTP (`Host`):
  a small file made by ffmpeg whose picture and sound are the episode it truly is (`Codec`).
- A MacCMS collection site of the form of Jisu (`Title`): the record of a title in its API, and the HLS of every episode
  on the host, encrypted with AES-128, its frame the height it is given; the player reads the head of a segment through the
  in-process web, the downloader the whole playlist over real HTTP.
- The alignment service is the model on the protocol of the real one (tests/plan_disk.Daemon) on real HTTP, with the
  stream of its events: the real `conform` module connects to it by the address of its settings, as on the server.
- The user is the HTTP API of the gateway: the voice-over groups, the profile of rules, the series and its sources, the
  sync, «Удалить», a switch.

What is checked is what is on disk (every sound of a file is the episode of its picture, every file carries the table of
its asset) and what the gateway gives the window."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import uvicorn
from fastapi.responses import StreamingResponse

from assembly import Options, assemble
from core import Bus, Envelope, Runner
from core import container as mkv
from core.db import Database
from app99_model import HOST as APP_HOST, App99Model, Card, Line, profile as app_profile
from alloha_model import PROFILE as ALLOHA_PROFILE, AllohaModel
from app_protocol_models import AppGetModel, AppMnModel, AppV7Model, GuluModel, YunDuoModel
from appdrama_model import DramaModel, Video as DramaVideo
from plan_disk import Codec, Daemon, Host, _run
from plan_world import truth_of
from schema_db import migrated
from sources_support import Web

STAND_LIMIT_S = 120.0  # a stand whose sync does not end within this waits for what no model gives


# The real service sends a keep-alive comment this often (modules/conform/client.py reads silence as a dead link).
KEEP_ALIVE_S = 15.0


class StreamingDaemon(Daemon):
    """The model of the alignment service with the stream of its events (`/events`): the snapshot of its jobs when a
    client connects, then every job that changed — what the real `conform` module mirrors."""

    def __init__(self, codec: Codec) -> None:
        super().__init__(codec)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.changed: asyncio.Event | None = None
        self.version = 0
        self.sent: dict[str, int] = {}

        @self.app.get("/events")
        async def events():
            self.loop = asyncio.get_running_loop()
            self.changed = asyncio.Event()

            async def stream():
                yield _event("conform.snapshot", {"items": list(self.jobs.values())})
                seen = self.version
                while True:
                    try:
                        await asyncio.wait_for(self.changed.wait(), KEEP_ALIVE_S)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"  # the service's heartbeat: a silent stream is a dead one to the client
                        continue
                    self.changed.clear()
                    items = [job for job in self.jobs.values() if self.sent.get(job["id"], 0) > seen]
                    seen = self.version
                    if items:
                        yield _event("conform.changed", {"items": items})
            return StreamingResponse(stream(), media_type="text/event-stream")

    def _job(self, kind, label, dubs, results, sources=()):
        job = super()._job(kind, label, dubs, results, sources)
        self._tell(job)
        return job

    def release(self) -> list[dict]:
        ended = super().release()
        for job in ended:
            self._tell(job)
        return ended

    def _tell(self, job: dict) -> None:
        """A job made or changed goes to the stream of events."""
        self.version += 1
        self.sent[job["id"]] = self.version
        if self.loop is not None and self.changed is not None:
            self.loop.call_soon_threadsafe(self.changed.set)


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class _Server(uvicorn.Server):
    """The server tells that it listens: its start is the signal, not a pause."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self.ready = threading.Event()

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        self.ready.set()


class Served:
    """An ASGI app on real HTTP in a thread of its own: the address is known once the server says it started."""

    def __init__(self, app) -> None:
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        # The loop of asyncio, not uvloop: uvicorn would set its policy for the whole process, the tests' too.
        self.server = _Server(uvicorn.Config(app, log_level="warning", lifespan="on", loop="asyncio", ws="none"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        if not self.server.ready.wait(30):
            raise RuntimeError("the model of the alignment service did not start")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


@dataclass
class Release:
    """A release page of the site: its voice-over and its episodes (number → what the episode truly is, its height)."""

    slug: str
    voice: str
    episodes: dict[int, tuple[int, int]] = field(default_factory=dict)
    # The compilations of the release: (first, last) episode → its height. Its entry is numbered apart (1000 + first).
    compilations: dict[tuple[int, int], int] = field(default_factory=dict)
    down: bool = False  # the page does not open (the site is down): every read of it fails
    # The episodes whose player stopped giving the video: the row stays on the page, its video does not play.
    silent: set[int] = field(default_factory=set)

    @property
    def url(self) -> str:
        return f"https://1.ruchime.org/release/{self.slug}"


@dataclass
class Title:
    """A title of a MacCMS collection site (Jisu): its HLS list, an episode by number → what it truly is."""

    number: int
    episodes: dict[int, int] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://jszyapi.com/index.php/vod/detail/id/{self.number}.html"


HLS_KEY = bytes(range(16))


# The application of each protocol on the stand, and the code of its one admitted line: a line is one player whichever
# application lists it, so each has a code of its own here.
APP_OF = {"appdrama": "huayi", "appget": "moli", "gulu": "gulu", "yunduo": "yunduo", "appmn": "appmn", "appv7": "xiaozhu"}
LINE_OF = {"huayi": "DRAMA", "moli": "MOLI", "gulu": "GULU", "yunduo": "YUNDUO", "appmn": "MINUO", "xiaozhu": "XIAOZHU"}


class Site:
    """The site and the player as the real parser and the real player read them; the files on the host."""

    def __init__(self, root: str, codec: Codec, host: Host) -> None:
        self.root = root
        self.codec = codec
        self.host = host
        self.releases: dict[str, Release] = {}
        self.ids: dict[tuple[str, int], int] = {}
        self.titles: dict[int, Title] = {}
        self.heads: list[str] = []  # the heads of segments the player asked for: what measuring a frame cost
        # A video application (tests/app99_model.py): its cards list a line of a parser whose streams are on the host.
        self.app = App99Model(parsers=[9])
        self.app.line("BLU", "蓝光", parsers="9")
        # The registry of the admitted lines of the application of the stand: its one line is a player.
        self.app_lines = os.path.join(root, "app-lines")
        os.makedirs(self.app_lines, exist_ok=True)
        with open(os.path.join(self.app_lines, "banri.json"), "w", encoding="utf-8") as registry:
            json.dump({"app": "banri", "admitted": {"BLU": {}}, "refused": {}}, registry)
        # The applications of every other protocol the service reads, each with one admitted line of its own code.
        self.drama = DramaModel()
        self.others = {"appget": AppGetModel(), "gulu": GuluModel(), "yunduo": YunDuoModel(), "appmn": AppMnModel(),
                       "appv7": AppV7Model()}
        # A title of yummyani.me and its player Alloha (tests/alloha_model.py), the CDN of which serves an HLS of the host.
        self.alloha = AllohaModel(voices={})
        self.yummy: dict[str, tuple[int, str, list[dict]]] = {}
        for name in ("huayi", "moli", "gulu", "yunduo", "appmn", "xiaozhu"):
            with open(os.path.join(self.app_lines, f"{name}.json"), "w", encoding="utf-8") as registry:
                json.dump({"app": name, "admitted": {LINE_OF[name]: {}}, "refused": {}}, registry)

    def profiles(self) -> list:
        """The profiles of every application the stand models."""
        return [app_profile(), self.drama.profile(), *(model.profile() for model in self.others.values())]

    def protocol_card(self, protocol: str, number: int, name: str, episodes: dict[int, int]) -> str:
        """A card of an application of this protocol with its one admitted line: an episode (number → height) is a playlist
        on the host its stream names. The address of the card as its source is added."""
        app = APP_OF[protocol]
        made = []
        for episode, height in sorted(episodes.items()):
            folder, token = f"{app}-{number}-ep{episode}", f"{app}-tok-{number}-{episode}"
            self._hls(folder, truth_of(1, episode), height)
            made.append((f"第{episode}集", token, self.host.url(folder + "/index.m3u8")))
        if protocol == "appdrama":
            self.drama.cards[number] = (name, 2021, [DramaVideo(LINE_OF[app], LINE_OF[app], label, token)
                                                     for label, token, _ in made])
            self.drama.plays.update({(LINE_OF[app], token): (address, {}) for _label, token, address in made})
        else:
            self.others[protocol].card(number, name, LINE_OF[app], made)
        return f"app://{app}/{number}"

    def alloha_title(self, slug: str, number: int, name: str, voice: str, episodes: dict[int, int]) -> str:
        """A title of yummyani.me whose voice-over plays in Alloha: an episode (number → height) an unencrypted HLS on the
        host its CDN serves. The address of the title as a source."""
        listed = self.alloha.voices.setdefault("158", {}).setdefault(1, [])
        videos = []
        for episode, height in sorted(episodes.items()):
            folder = f"alloha-{slug}-ep{episode}"
            self._hls(folder, truth_of(1, episode), height, encrypted=False)
            listed.append(episode)
            self.alloha.served[f"9{episode}"] = (os.path.join(self.root, folder), height)
            iframe = f"//alloha.yani.tv/?token_movie=8bf4&translation=158&season=1&episode={episode}&token=8b55"
            videos.append({"video_id": number * 1000 + episode, "number": str(episode), "iframe_url": iframe,
                           "data": {"player": "Плеер Alloha", "dubbing": f"Озвучка {voice}", "player_id": 1},
                           "index": episode})
        self.yummy[slug] = (number, name, videos)
        return f"https://old.yummyani.me/catalog/item/{slug}"

    def title(self, number: int) -> Title:
        return self.titles.setdefault(number, Title(number))

    def hls_episode(self, number: int, episode: int, *, height: int = 1080) -> None:
        """An episode of a title: its video in a picture of this height, cut into encrypted segments on the host."""
        truth = truth_of(1, episode)
        self.titles[number].episodes[episode] = truth
        self._hls(self._hls_folder(number, episode), truth, height)

    def app_card(self, number: int, name: str, episodes: dict[int, int], *, files: bool = False) -> None:
        """A card of the application with its line of a parser: an episode (number → height) is a playlist on the host the
        parser gives for its token — or, with `files`, an MP4 at an address without an extension, as a parser gives one;
        the search of the application gives the card."""
        items = []
        for episode, height in sorted(episodes.items()):
            folder, token = f"app-{number}-ep{episode}", f"tok-{number}-{episode}"
            if files:
                self.app.parses[(9, token)] = self.host.url(self._mp4(folder, truth_of(1, episode), height))
            else:
                self._hls(folder, truth_of(1, episode), height)
                self.app.parses[(9, token)] = self.host.url(folder + "/index.m3u8")
            items.append((f"第{episode}集", token))
        self.app.cards[number] = Card(number, name, [Line("BLU", items)], sub="Model Title")
        self.app.found = [{"id": number, "name": name, "sub": "Model Title", "year": "2021", "area": "大陆",
                           "class": "动画", "remarks": f"更新至第{len(items)}集", "blurb": "", "pic": ""}]

    def _mp4(self, name: str, truth: int, height: int) -> str:
        """A video in a picture of this height as one MP4 on the host, its name with no extension; the name."""
        path = os.path.join(self.root, name + "-video")
        if not os.path.exists(path):
            source = path + ".source.mp4"
            self.codec.make(source, [truth])
            _run("-i", source, "-vf", f"scale={height * 16 // 9 // 2 * 2}:{height}", "-c:v", "libx264", "-preset",
                 "ultrafast", "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", path)
            os.remove(source)
        return name + "-video"

    def _hls(self, name: str, truth: int, height: int, *, encrypted: bool = True) -> None:
        folder = os.path.join(self.root, name)
        if os.path.exists(os.path.join(folder, "index.m3u8")):
            return
        os.makedirs(folder)
        source = os.path.join(folder, "source.mp4")
        self.codec.make(source, [truth])
        keyed = []
        if encrypted:
            with open(os.path.join(folder, "enc.key"), "wb") as key:
                key.write(HLS_KEY)
            info = os.path.join(folder, "key.info")
            with open(info, "w") as written:
                written.write(f"enc.key\n{os.path.join(folder, 'enc.key')}\n")
            keyed = ["-hls_key_info_file", info]
        # H.264, as the sites give it: its parameters ride in every segment, which the player reads the frame from.
        _run("-i", source, "-vf", f"scale={height * 16 // 9 // 2 * 2}:{height}", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "copy",
             "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod", *keyed,
             "-hls_segment_filename", os.path.join(folder, "seg%d.ts"), os.path.join(folder, "index.m3u8"))
        os.remove(source)

    @staticmethod
    def _hls_folder(number: int, episode: int) -> str:
        return f"jisu-{number}-ep{episode}"

    def _title_record(self, title: Title) -> str:
        items = [(f"第{episode}集", episode) for episode in sorted(title.episodes)]
        lists = {"jsyun": "#".join(f"{label}$https://vv.jisuzyv.com/play/{title.number}-{episode}" for label, episode in items),
                 "jsm3u8": "#".join(f"{label}${self.host.url(self._hls_folder(title.number, episode) + '/index.m3u8')}"
                                    for label, episode in items)}
        return json.dumps({"code": 1, "msg": "数据列表", "list": [{
            "vod_id": title.number, "vod_name": f"师兄{title.number}", "vod_play_from": "$$$".join(lists),
            "vod_play_url": "$$$".join(lists.values())}]}, ensure_ascii=False)

    def _yummy_api(self, rest: str) -> dict | None:
        """`/api/anime/<address>` (the number and the names of a title) and `/api/anime/<number>/videos`."""
        for slug, (number, name, videos) in self.yummy.items():
            if rest == slug:
                return {"response": {"anime_id": number, "anime_url": slug, "title": name, "other_titles": []}}
            if rest == f"{number}/videos":
                return {"response": videos}
        return None

    def _host_file(self, request: httpx.Request) -> httpx.Response:
        """A file of the host as the in-process web gives it to the player: the part a range asks for, or all of it."""
        name = request.url.path.lstrip("/")
        path = os.path.join(self.root, name)
        if name in self.host.missing or not os.path.isfile(path):
            return httpx.Response(404)
        with open(path, "rb") as file:
            body = file.read()
        asked = request.headers.get("Range", "")
        if asked.startswith("bytes=0-"):
            self.heads.append(name)
            return httpx.Response(206, content=body[: int(asked.removeprefix("bytes=0-")) + 1])
        return httpx.Response(200, content=body)

    def release(self, slug: str, voice: str) -> Release:
        return self.releases.setdefault(slug, Release(slug, voice))

    def episode(self, slug: str, number: int, *, truth: int | None = None, height: int = 720) -> None:
        """An episode of a release; `truth` is what its video is (by default the episode of its number)."""
        release = self.releases[slug]
        truth = truth_of(1, number) if truth is None else truth
        release.episodes[number] = (truth, height)
        self.ids.setdefault((slug, number), 700000 + len(self.ids) + 1)
        name = f"{slug}-ep{number}-truth{truth}-file.mp4"  # no number before the extension: the player reads none from it
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            self.codec.make(path, [truth])

    def compilation(self, slug: str, first: int, last: int, *, height: int = 2160) -> None:
        """A compilation of episodes `first`…`last` of a release: one video that holds them one after another."""
        self.releases[slug].compilations[(first, last)] = height
        self.ids.setdefault((slug, 1000 + first), 700000 + len(self.ids) + 1)
        path = os.path.join(self.root, self._compilation_file(slug, first, last))
        if not os.path.exists(path):
            self.codec.make(path, [truth_of(1, number) for number in range(first, last + 1)])

    @staticmethod
    def _compilation_file(slug: str, first: int, last: int) -> str:
        return f"{slug}-from{first}-to{last}-file.mp4"

    def gone(self, slug: str, number: int, gone: bool = True) -> None:
        """The file of an episode answers 404 (gone for good) — or is back."""
        truth, _height = self.releases[slug].episodes[number]
        name = f"{slug}-ep{number}-truth{truth}-file.mp4"
        (self.host.missing.add if gone else self.host.missing.discard)(name)

    def drop(self, slug: str, number: int) -> None:
        self.releases[slug].episodes.pop(number, None)

    def drop_compilation(self, slug: str, first: int) -> None:
        release = self.releases[slug]
        release.compilations = {span: height for span, height in release.compilations.items() if span[0] != first}

    def _release_page(self, release: Release) -> str:
        entries = [{"id": 93000 + number, "number": number, "compilation_from": None, "compilation_to": None,
                    "title": f"Серия {number}", "is_vip": False, "is_trailer": False, "date": "09 May 2026"}
                   for number in sorted(release.episodes)]
        entries += [{"id": 94000 + first, "number": 1000 + first, "compilation_from": first, "compilation_to": last,
                     "title": f"Серии {first}-{last}", "is_vip": False, "is_trailer": False, "date": "09 May 2026"}
                    for first, last in sorted(release.compilations)]
        return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>Сериал {release.slug} — Ruchime</title></head>
<body>
<div class="info-meta">
<span class="info-meta-key">Озвучка</span>
<span class="info-meta-val">{release.voice}</span>
</div>
<script>
const ALL_EPS = {json.dumps(entries, ensure_ascii=False)};
</script>
</body></html>
"""

    def _episode_page(self, release: Release, number: int) -> str:
        video = self.ids[(release.slug, number)]
        return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>Сериал {number} серия — Ruchime</title></head>
<body>
<div class="player-source-row">
<button class="source-btn" data-player="allvideo" onclick="switchSource('https://fsst.online/embed/{video}/',this)">AllVideo</button>
</div>
</body></html>
"""

    def _embed(self, video: int) -> str:
        found = next(((slug, number) for (slug, number), each in self.ids.items() if each == video), None)
        if found is None:
            return ""
        slug, number = found
        release = self.releases[slug]
        if number in release.silent:
            return ""
        if number > 1000:
            span = next((each for each in release.compilations if each[0] == number - 1000), None)
            if span is None:
                return ""
            height, file = release.compilations[span], self.host.url(self._compilation_file(slug, *span))
        elif number in release.episodes:
            truth, height = release.episodes[number]
            file = self.host.url(f"{slug}-ep{number}-truth{truth}-file.mp4")
        else:
            return ""
        return f"""<!DOCTYPE html><html><head><title>Сериал</title></head><body>
<script>var player = new Playerjs({{ id: 'Video{video}', file:"[{height}p]{file}" }});</script>
</body></html>"""

    def web(self) -> Web:
        def answer(request: httpx.Request):
            path = request.url.path.strip("/")
            if str(request.url).startswith(APP_HOST):
                return self.app.handle(request)
            if request.url.host in ("navigator.model", "drama.model"):
                return self.drama.handle(request)
            model = next((each for each in self.others.values() if each.owns(str(request.url))), None)
            if model is not None:
                return model.handle(request)
            if request.url.host == "127.0.0.1":
                return self._host_file(request)
            if request.url.host == "old.yummyani.me" and path.startswith("api/anime/"):
                found = self._yummy_api(path.removeprefix("api/anime/"))
                return (200, json.dumps(found, ensure_ascii=False)) if found is not None else (404, "")
            if request.url.host == "jszyapi.com" and path == "api.php/provide/vod":
                title = self.titles.get(int(request.url.params.get("ids") or 0))
                return (200, self._title_record(title)) if title is not None else (200, json.dumps({"code": 1, "list": []}))
            if request.url.host == "fsst.online" and path.startswith("embed/"):
                text = self._embed(int(path.split("/")[1]))
                return (200, text) if text else (404, "")
            parts = path.split("/")
            if request.url.host == "1.ruchime.org" and len(parts) >= 2 and parts[0] == "release":
                release = self.releases.get(parts[1])
                if release is None:
                    return 404, ""
                if release.down:
                    return 503, ""
                if len(parts) == 2:
                    return 200, self._release_page(release)
                number = int(parts[2].removeprefix("episode-"))
                known = number in release.episodes or any(first == number - 1000 for first, _ in release.compilations)
                return (200, self._episode_page(release, number)) if known else (404, "")
            return 404, ""
        return Web().route("", answer).route("", answer, method="POST")


class AppStand:
    """The application with its models (see the module)."""

    def __init__(self, root: str) -> None:
        self.root = root
        for name in ("site", "library", "work", "cache"):
            os.makedirs(os.path.join(root, name), exist_ok=True)
        self.codec = Codec()
        self.host = Host(os.path.join(root, "site"))
        self.site = Site(os.path.join(root, "site"), self.codec, self.host)
        self.daemon = StreamingDaemon(self.codec)
        self.daemon_http = Served(self.daemon.app)
        self.library = os.path.join(root, "library")
        self.db_path = migrated(os.path.join(root, "app.db"))
        self.profiles: dict[str, int] = {}  # the profile of each voice-over: two sources of one voice-over share it
        # The time of day as the sources tell the age of a table by it: a scenario moves it to let a page be read again.
        self.now = datetime.now(timezone.utc)

    async def start(self, before_started=None) -> None:
        self.bus = Bus()
        self.db = Database(self.db_path)
        self.gateway, self.modules = assemble(self.bus, self.db, Options(
            db_path=self.db_path, auth_required=False, cookie_secure=False,
            torrent_cache_dir=os.path.join(self.root, "cache"), table_clock=lambda: self.now, apps=self.site.profiles(),
            app_lines_dir=self.site.app_lines))
        self.runner = Runner(self.bus, self.modules)
        await self.runner.start()
        self.module("sources")._http = self.site.web().client()
        # Alloha is asked as its player asks, by the client of its model, with the profile of the model.
        self.module("sources").players.get("alloha").configure(alloha_profile=ALLOHA_PROFILE,
                                                                alloha_client=self.site.alloha.client)
        if before_started is not None:
            before_started()
        self.bus.publish(Envelope(topic="system.started", kind="event"))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.gateway.app), base_url="http://stand")

    def module(self, name: str):
        return next(each for each in self.modules if each.name == name)

    async def stop(self) -> None:
        await self.api.aclose()
        await self.runner.stop()

    def close(self) -> None:
        self.daemon_http.close()
        self.host.close()

    # --- the user, through the API ------------------------------------------------------------------------------

    async def call(self, method: str, path: str, body=None) -> dict:
        response = await self.api.request(method, path, json=body)
        assert response.status_code < 400, (method, path, response.status_code, response.text[:400])
        return response.json() if response.content else {}

    async def configure(self, groups: list[str], languages: dict[str, str] | None = None) -> None:
        """The settings the user makes once: the work folder and the address of the alignment service, the groups of
        the voice-overs, each made with the language of its voice-over (`languages`: group → ISO 639-2; Russian for the
        others)."""
        work = os.path.join(self.root, "work")
        connected = self.bus.subscribe("conform.daemon.up")
        await self.call("PUT", "/api/conform/settings", {"url": self.daemon_http.url, "work_dir": work,
                                                          "work_dir_remote": work})
        await asyncio.wait_for(connected.queue.get(), STAND_LIMIT_S)
        saved = await self.call("PUT", "/api/voices/settings", {"priority": ["vk", "cvh", "allvideo", "kodik"],
                                                         "voice_groups": {each: [] for each in groups},
                                                         "voice_languages": {each: (languages or {}).get(each, "rus")
                                                                             for each in groups}})
        assert saved["saved"], saved["problems"]

    async def profile(self, voice: str) -> int:
        """A profile of rules, as the user makes one for a source: «Серия N» numbers the row, and the voice-over of the
        source is the tag the rule gives it (the constructor alone names a voice-over). Made once for a voice-over."""
        if voice in self.profiles:
            return self.profiles[voice]
        made = await self.call("POST", "/api/parser-profiles", {"name": f"серии {voice}"})
        self.profiles[voice] = made["id"]
        single = json.dumps([{"type": "text", "value": "Серия "}, {"type": "number"}], ensure_ascii=False)
        span = json.dumps([{"type": "text", "value": "Серии "}, {"type": "number"}, {"type": "text", "value": "-"},
                           {"type": "number"}], ensure_ascii=False)
        # A rule acts only on the titles of its form: a rule that matched gives the voice-over and stops the others.
        for name, kind, blocks in (("компиляция", "extract_range", span), ("серия", "extract_single", single)):
            actions = [{"action_type": kind, "action_pattern": blocks},
                       {"action_type": "assign_voiceover", "action_pattern": voice}]
            await self.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
                "name": name, "action_pattern": json.dumps(actions, ensure_ascii=False), "continue_after_match": False,
                "conditions": [{"condition_type": "contains", "pattern": blocks, "logical_operator": "AND"}]})
        return made["id"]

    async def series(self, name: str, slugs: list[str]) -> int:
        """A series of the releases of these slugs, each source with a profile of its own voice-over."""
        sources = [{"url": self.site.releases[slug].url, "source_type": "web_video",
                    "parser_profile_id": await self.profile(self.site.releases[slug].voice)} for slug in slugs]
        made = await self.call("POST", "/api/series", {"name": name, "name_en": name, "site": "ruchime",
                                                        "save_path": self.library, "sources": sources})
        return made["series_id"]

    def later(self, hours: float = 2) -> None:
        """Time passes: the tables of the pages are older than their term, the next sync reads the site again."""
        self.now += timedelta(hours=hours)

    async def sync(self, series_id: int) -> dict:
        """A sync as the window starts it, to its end: the run of the series closes when the planner has no work left."""
        ended = self.bus.subscribe("scan.run.changed")
        await self.call("POST", f"/api/series/{series_id}/scan")
        await self._run_end(ended, series_id)
        return await self.call("GET", f"/api/series/{series_id}/plan")

    async def _run_end(self, ended, series_id: int) -> None:
        while True:
            packet = (await asyncio.wait_for(ended.queue.get(), STAND_LIMIT_S)).payload
            if packet["series_id"] == series_id and not packet["active"]:
                return

    async def restart(self) -> None:
        """The process stops (whatever it is doing) and starts again on the same database and folders. Whoever waits for
        the end of a run after it subscribes before the start: `until_run_ends` is told the end."""
        await self.stop()
        await self.start(before_started=lambda: setattr(self, "_ended", self.bus.subscribe("scan.run.changed")))

    async def until_run_ends(self, series_id: int) -> None:
        await self._run_end(self._ended, series_id)

    # --- what is on disk ------------------------------------------------------------------------------------------

    def files(self) -> list[str]:
        found = []
        for folder, dirs, names in os.walk(self.library):
            dirs[:] = [each for each in dirs if not each.startswith(".")]
            found += [os.path.relpath(os.path.join(folder, name), self.library) for name in names
                      if name.endswith(".mkv") or name.endswith(".mkv.off")]
        return sorted(found)

    async def check(self, series_id: int) -> dict:
        """The library is the asset of the plan; every sound of a file is the episode of its picture; every file carries
        its row of the table."""
        view = await self.call("GET", f"/api/series/{series_id}/plan")
        names = sorted(each["name"] for each in view["assets"])
        assert self.files() == names, (self.files(), names)
        for name in names:
            path = os.path.join(self.library, name)
            held = await mkv.contents(path)
            picture = await asyncio.to_thread(self.codec.picture, path)
            for track in held.audio:
                sound = await asyncio.to_thread(self.codec.sound, path, track.index)
                assert sound == picture, f"{name}: a sound of another episode"
            assert (await mkv.provenance_of(path))["name"] == name
        return view
