"""Модуль sources — источники раздач: парсеры трекеров (Р-9).

Queries:
  sources.parse {url}            → {service, title, releases[]} — the releases of a tracker; a page of a site
                                   (ruchime, dunhuatv, animakima, animesss, pvashow, yummyani, a VK channel) is read by
                                   `sources.table.get`, not parsed
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.torrent_file.drop {torrent_id} — чистка кэша (каскад Р-19)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)
  sources.tracker.resolve {url}  → {tracker|null} (tracker_info деталей)

Tables of site pages (the one way to read a site page; sites are registered in `sites`, the core is `table.py`):
  sources.table.get {url, refresh?} → TableGetReply {url, state: ready | reading | failed | absent,
                                   table?, reason?}. A complete table within the term (term.py) is `ready`. Otherwise the page is read — by one
                                   task however many ask — and the reply is `reading` at once, with the last
                                   complete table when there is one; the end comes as the events below.
                                   `failed` in the reply means no read began (`site_not_found`: no site for the
                                   address). `url` of the reply is the canonical address the site gives: the key
                                   of the table and the `url` of the events. `refresh` false reads nothing and
                                   says how the page stands: `reading` while a read goes, `failed` (with the
                                   reason) when the last read failed, `ready` for a complete table however old,
                                   `absent` when nothing was read; with the last complete table when there is one.
  sources.table.read {url} command: read the page now, term or not, unless a read of it is going; the read takes
                                   no raw data that an earlier read left for sharing (the listing of a VK channel)
  sources.table.cancel {url} → {cancelled}: the read of the page going now stops (the user stopped the sync that waits
                                   for it): it ends failed (`read_cancelled`) and is not begun again at the start; the
                                   last complete table still serves. False when no read of it goes.
  sources.video.fetch {url, target, max_height?, connections} → {ok, seconds, longest, height} | {ok: false, error,
                                   final}: the file of a link whose player downloads it itself, into `target`; how far
                                   it is goes as `sources.video.progress {target, phase, progress, ...}`. A refusal the
                                   player knows to be final (no such file) is `final`.
  sources.video.fetch.stop {target} → {stopped}: that download stops (its owner gave the file up).
  sources.site.resolve {url} → {site, canonical_url}: which site reads the address and the canonical
                                   form of it (no network); both null when no site does
  sources.settings.get {} → SourcesSettingsView {trackers: [{id, display_name, mirrors}], readers: [{name, kind:
                                   tracker | site, domains}]}: the settings screen of the sources whole — the mirrors of
                                   each tracker, and the domains the service reads pages of now: every tracker with its
                                   domains and mirrors, every site of the registry with its own (no network)
  sources.video.stream {url, max_height?} → {handled, stream_url, headers, progressive, playlist} | {handled, error,
                                   transient}: the stream of a candidate link, asked for right before the download;
                                   `player_fetch` instead: the player downloads the file itself (sources.video.fetch).
                                   The player that owns the link (a VK player link with its key, an AllVideo embed,
                                   a Vibix entry, an item of CDN VideoHub or of Kodik) resolves it; any other
                                   address is not handled
  sources.players.list PlayersListRequest {urls} → PlayersListReply {players: [PlayerFacts]}: every player a row of a
                                   table can be of, as the order of sources names it, with where it is among the pages
                                   of `urls` (their stored tables, no network): its kind (`site`, `line`), the best
                                   height of its rows and its places — the sites whose pages hold its rows, or the
                                   applications that admit the line, each with how many of the pages are its. The
                                   players are those of the registry and the lines some application admits, one
                                   player a code whichever application lists it (`line:<code>`, named by the code;
                                   modules/sources/apps/lines.py)
  sources.apps.search {query, page?, app?} → AppSearchReply {apps: [AppResults]}: a page of the search of every video
                                   application (modules/sources/apps), or of the one named, as the application gives it,
                                   or the reason it gave none. A card picked is a source by its address
                                   `app://<application>/<card>`; its table is every episode of every line of the card,
                                   a row of the player `<application>:<line>` (players/app_lines.py)
  sources.term.get {} → {hours}: the term of what the sources answered (term.py), whole hours
  sources.term.set {hours} → {ok, hours, reason?}: a whole number of hours from one is stored (`sources_term_hours` of the
                                   settings) and is the term of every reader of the module at once; any other value is
                                   refused (`sources_term_invalid`) and the term stays
Events (owner: sources):
  sources.table.progress {url, phase, current, total}   how far a read is
  sources.table.ready {url, read_at}                    a complete table is stored and served
  sources.settings.changed SourcesSettingsView          the mirrors of a tracker were saved: every open settings
                                                        screen shows them without asking back
  sources.table.failed {url, reason}                    the read failed; the last complete table still serves
  sources.table.trace {url, site, kind: begin | end, …}  what a read does, for its dump (modules/runlog): `end` carries
                                                        the seconds of the read and what every player of the page gave
                                                        (rows by state, frames, the causes of the rows without a video)
Events in: settings.changed {key: sources_term_hours} — the term of what the sources answered (term.py): the tables of
pages, the listings of VK channels and the addresses parsers of applications gave are current for that many hours.
settings.changed {key: voices.players_off} — the players the owner switched off for every source: a read asks none of
them (ReadContext.off) and keeps their rows of the last read as they were; a player switched on is asked at the next
read of a page (the term, or a read the user asks for).
A failed read is not retried by itself and a consumer does not ask again on `failed`: the next request after a
failure begins a new read. Only a complete table is ever published. A stop in the middle of a read is no verdict:
the state `reading` is stored and `on_start` begins the read again.

Доставка — ТОЛЬКО через `delivery.Delivery` (единственный выход
наружу): она перебирает хосты-кандидаты (канонический хост ссылки →
зеркала трекера → хост как написан) и проверяет, что приехало ожидаемое.
Транспорт выбирается по трекеру: kinozal / rutracker — trackerauth
(сессии и челлендж — не наша забота); anilibria — официальный API через
httpx; astar / anilibria_tv — общий Playwright-пул (_browser_html,
ленивый запуск, контекст на запрос).

Между запросами к одному трекеру выдерживается пауза (уважение к
чужому сервису) — обеспечивается замком на сервис + минимальным
интервалом.
"""
from __future__ import annotations

import json
import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Callable, Iterable

import httpx

from core import BaseModule, compute, ids
from core import storage
from core.contracts.voices import PLAYERS_OFF_KEY
from core.contracts import (AppCard, AppResults, AppSearchReply, AppSearchRequest, ParsedDomains, PlayerFacts, PlayerPlace,
                            PlayersListReply, PlayersListRequest, SiteResolveRequest,
                            SourcesSettingsView, TableGetRequest, TrackerMirrors)
from core.deadlines import TABLE_READ_S
from core.db import Database
from core.envelope import Envelope
from core.http_limits import bounded_client
from core.logging import get_logger
from core.reasons import describe, reason

from . import anilibria, delivery, parsers
from .browser_pool import BrowserPool
from .apps import AppError, AppProfile, card_address
from .apps.app99 import App99Client
from .apps.appdrama import AppDramaClient
from .apps.appget import AppGetClient
from .apps.appmn import AppMnClient
from .apps.appv7 import AppV7Client
from .apps.gulu import GuluClient
from .apps.lines import LineRegistry, routes_of
from .apps.share139 import Share139Client
from .apps.yunduo import YunDuoClient
from .delivery import Delivery
from .parsers import SourceParseError
from .repository import SourcesRepository
from .term import DEFAULT_HOURS, TERM_KEY, Term, hours_of
from .players.alloha_session import AllohaRefused, Profile as AllohaProfile
from .players.app_lines import AppLines, AppPlayer, line_player_name
from .players.contract import StreamUnavailable
from .players.registry import PlayerRegistry
from .players.streams import resolve_stream
from .sites import Site, SiteError, SiteRegistry
from .sites.apps import AppSite
from .sites.apps.site import APP_IN_FLIGHT
from .sites.builtin import builtin_sites
from .table import TableReader, utc_now

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру
VK_API_BASE = "https://api.vk.com/method/"


# The clients of the protocols of the video applications, by the `protocol` of a profile (modules/sources/apps).
APP_PROTOCOLS = {"app99": App99Client, "appdrama": AppDramaClient, "appget": AppGetClient, "gulu": GuluClient,
                 "yunduo": YunDuoClient, "appmn": AppMnClient, "appv7": AppV7Client, "share139": Share139Client}

# The connections the client of the sources opens and keeps open between requests. A read of a card of an application
# has up to APP_IN_FLIGHT rows going, each one request at a time, and the applications read side by side: kept for the
# rows of a dozen of them, a row asks over an open connection instead of a new TCP and TLS handshake (measured on 半日:
# a row 5.2 s with 20 kept, 2.8 s with 100), and as many again are left to the reads of the other sources.
CONNECTIONS = httpx.Limits(max_connections=2 * 12 * APP_IN_FLIGHT, max_keepalive_connections=2 * 12 * APP_IN_FLIGHT,
                           keepalive_expiry=30.0)

class SourcesModule(BaseModule):
    name = "sources"

    # Браузерная доставка astar/anilibria_tv (Р-9). UA/viewport — как в
    # старом firefox-парсере (защита трекеров фингерпринтит браузер).
    _BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                   "Gecko/20100101 Firefox/125.0")
    BROWSER_TIMEOUT = 30000  # мс на загрузку/JS-челлендж в браузере

    def __init__(self, bus, db: Database, *,
                 torrent_cache_dir: str = "torrent_cache",
                 sites: Iterable[Site] | None = None,
                 table_clock: Callable[[], datetime] = utc_now,
                 table_safeguard_s: float = TABLE_READ_S, read_recent: int | None = None,
                 apps: Iterable[AppProfile] = (), app_lines_dir: str | None = None,
                 alloha_profile: AllohaProfile | None = None) -> None:
        self.repo = SourcesRepository(db)
        # The downloads players make themselves, by their files (`sources.video.fetch`).
        self._transfers: dict[str, asyncio.Future] = {}
        self._cache_dir = torrent_cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        # The one browser of the module, started at the first page that needs it and stopped after its last user
        # (browser_pool.py): the players and the tracker pages take their contexts of it.
        self.browsers = BrowserPool(get_logger("sources"))
        wiring = dict(
            browser_getter=self.browsers.browser,
            key_path=os.path.abspath(os.path.join(torrent_cache_dir,
                                                  "vibix-key.json")),
            manifest_dir=os.path.abspath(os.path.join(torrent_cache_dir,
                                                      "manifests")),
            alloha_profile=alloha_profile)
        # One object per player for the whole module: the sites read with them and the streams are resolved by them.
        self.players = PlayerRegistry.standard(**wiring)
        # The video applications the environment describes: each a player of its lines and a site of its cards.
        self.apps: list[AppPlayer] = []
        # One term of what the sources answered for every reader of the module; the setting changes it for all at once.
        self.term = Term()
        # The players switched off for every source (the setting of the voices module, kept here as the event says it).
        self.players_off: frozenset[str] = frozenset()
        for profile in apps:
            client = APP_PROTOCOLS.get(profile.protocol)
            if client is None:
                raise ValueError(f"application {profile.name}: unknown protocol {profile.protocol!r}")
            self.apps.append(AppPlayer(client(profile, lambda: self._http),
                                       registry=LineRegistry.load(profile.name, app_lines_dir), term=self.term))
        # A line of an application that another one parses when its own parser says no (apps/routes.json), where that
        # other application is configured.
        clients = {each.name: each.client for each in self.apps}
        for each in self.apps:
            each.through = {line: (clients[name], code) for line, (name, code) in routes_of(each.name).items()
                            if name in clients}
        # One player of the lines of every application: a line is one player whichever application lists it.
        self.lines = AppLines(self.apps)
        if self.apps:
            self.players.register(self.lines)
        # The sites belong to this instance, not to the process: a site is added by registering it (builtin.py).
        self.sites = SiteRegistry([*(builtin_sites() if sites is None else sites),
                                   *(AppSite(app, self.lines) for app in self.apps)])
        self.sites.configure(players=self.players, vk_call=self._vk_call, term=self.term)
        self.tables = TableReader(
            self.repo, self.sites, lambda topic, payload: self.publish_event(topic, payload), lambda: self._http,
            players=self.players, clock=table_clock, safeguard_s=table_safeguard_s, recent=read_recent, term=self.term,
            holding=self.browsers.held, off=lambda: self.players_off)
        super().__init__(bus)
        # Late binding on purpose: tests replace the transport, not the bus.
        self._delivery = Delivery(
            request=lambda topic, payload, **kw: self.request(topic, payload,
                                                              **kw),
            http=lambda: self._http,
            browser_html=lambda service, url: self._browser_html(service, url),
            log=self.log)

    def register(self) -> None:
        self.handle("sources.parse", self.on_parse)
        self.handle("sources.torrent_file.get", self.on_torrent_file)
        self.handle("sources.torrent_file.drop", self.on_torrent_file_drop)
        self.handle("sources.trackers.list", self.on_trackers_list)
        self.handle("sources.tracker.resolve", self.on_tracker_resolve)
        self.handle("sources.tracker.set_mirrors", self.on_set_mirrors)
        self.handle("sources.video.stream", self.on_video_stream)
        self.handle("sources.video.fetch", self.on_video_fetch, concurrent=True)
        self.handle("sources.video.fetch.stop", self.on_video_fetch_stop)
        self.handle("sources.table.get", self.on_table_get)
        self.handle("sources.table.read", self.on_table_read)
        self.handle("sources.table.cancel", self.on_table_cancel)
        self.handle("sources.site.resolve", self.on_site_resolve)
        self.handle("sources.settings.get", self.on_settings_get)
        self.handle("sources.apps.search", self.on_apps_search)
        self.handle("sources.players.list", self.on_players_list)
        self.handle("sources.term.get", self.on_term_get)
        self.handle("sources.term.set", self.on_term_set)
        self.handle("settings.changed", self.on_settings_changed)

    async def on_start(self) -> None:
        self._http = bounded_client(timeout=20.0, follow_redirects=True, limits=CONNECTIONS)
        try:
            stored = (await self.request("settings.value.get", {"key": TERM_KEY})).get("value")
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves the term at its default
            stored = None
        self._set_term(stored)
        try:
            players_off = (await self.request("settings.value.get", {"key": PLAYERS_OFF_KEY})).get("value")
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves every player asked, as the term stays at its default
            players_off = None
        self._set_players_off(players_off)
        try:
            await self.tables.resume()
        except Exception:  # noqa: BLE001 — the other duties of the module do not wait for the table reads
            # Consumers ask for their tables again when they start; a page left marked "reading" is read at the next ask.
            self.log.exception("reads of tables interrupted by a stop were not resumed")

    async def on_stop(self) -> None:
        await self.tables.close()
        await self._http.aclose()
        await self.browsers.shutdown()

    async def on_term_get(self, env: Envelope) -> dict:
        return {"hours": self.term.hours}

    async def on_term_set(self, env: Envelope) -> dict:
        hours = hours_of(env.payload.get("hours"))
        if hours is None:
            return {"ok": False, "hours": self.term.hours,
                    "reason": reason("sources_term_invalid", value=str(env.payload.get("hours")))}
        await self.request("settings.value.set", {"key": TERM_KEY, "value": str(hours)})
        self._set_term(hours)
        return {"ok": True, "hours": hours}

    async def on_settings_changed(self, env: Envelope) -> None:
        if env.payload.get("key") == TERM_KEY:
            self._set_term(env.payload.get("value"))
        elif env.payload.get("key") == PLAYERS_OFF_KEY:
            self._set_players_off(env.payload.get("value"))

    def _set_players_off(self, value) -> None:
        """The players a setting names: a JSON list of names; anything else leaves every player asked."""
        try:
            names = json.loads(value) if value not in (None, "") else []
        except (TypeError, ValueError):
            names = None
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            self.log.warning("отключённые плееры «%s»: не список имён, опрашиваются все", value)
            names = []
        self.players_off = frozenset(names)

    def _set_term(self, value) -> None:
        hours = hours_of(value) if value not in (None, "") else DEFAULT_HOURS
        if hours is None:
            self.log.warning("срок актуальности опроса источников «%s» не число часов от 1: остаётся %d ч", value,
                             self.term.hours)
            return
        if hours != self.term.hours:
            self.log.info("срок актуальности опроса источников: %d ч", hours)
        self.term.hours = hours

    # --- бережный доступ к трекеру ------------------------------------------------

    async def _polite(self, service: str) -> None:
        since = time.monotonic() - self._last_request.get(service, -1e9)
        if since < REQUEST_MIN_INTERVAL:
            await asyncio.sleep(REQUEST_MIN_INTERVAL - since)
        self._last_request[service] = time.monotonic()

    # --- queries --------------------------------------------------------------------

    async def on_trackers_list(self, env: Envelope) -> list[dict]:
        return await self.repo.all_trackers()

    async def on_tracker_resolve(self, env: Envelope) -> dict:
        """Трекер по URL серии (контракт tracker_info деталей, Р-19)."""
        return {"tracker": await self.repo.resolve(env.payload["url"])}

    async def on_set_mirrors(self, env: Envelope) -> dict:
        """Зеркала трекера (вкладка настроек, Р-22)."""
        await self.repo.set_mirrors(env.payload["tracker_id"],
                                    env.payload["mirrors"])
        self.publish_event("sources.settings.changed", await self._settings())
        return {"ok": True}

    async def on_torrent_file_drop(self, env: Envelope) -> None:
        """Каскад Р-19: удаление серии чистит кэш её .torrent-файлов
        (команда от torrents — torrent_id знает только он)."""
        import os
        path = self._cache_path(env.payload["torrent_id"])
        await storage.call(
            lambda: os.path.isfile(path) and os.remove(path), owns=path)

    async def on_parse(self, env: Envelope) -> dict:
        url = env.payload["url"]
        tracker = await self.repo.resolve(url)
        if not tracker:
            raise SourceParseError(reason("tracker_unknown_url", url=url))
        service = tracker["canonical_name"]
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            await self._polite(service)
            result = await self._parse_by_service(service, url,
                                                  tracker["mirrors"])
        # torrent_id — констрейнт данных (core/ids, Р-10/Р-22); считаем
        # здесь один раз, scan и форма добавления используют готовый
        for r in result.get("releases", []):
            link_for_id = r.get("link") or r.get("magnet")
            if link_for_id and "torrent_id" not in r:
                r["torrent_id"] = ids.torrent_id(link_for_id,
                                                 r.get("date_marker"))
        result["service"] = service
        result["ui_features"] = tracker["ui_features"]
        result["tracker"] = tracker
        return result

    async def _parse_by_service(self, service: str, url: str,
                                mirrors: list[str]) -> dict:
        if service == "anilibria":
            alias = anilibria.alias_from_url(url)
            api = anilibria.api_base_from_url(url)
            delivered = await self._delivery.fetch(
                service, f"{api}/anime/releases/{alias}", expect="json",
                mirrors=mirrors, transport="http",
                accept=lambda data, _url: anilibria.release_to_result(data))
            return delivered.value

        if service in ("kinozal", "rutracker"):
            parse = (parsers.kinozal_parse if service == "kinozal"
                     else parsers.rutracker_parse)
            return await self._parse_page(service, url, mirrors, parse,
                                          transport="auth")

        if service in ("astar", "anilibria_tv"):
            parse = (parsers.astar_parse if service == "astar"
                     else parsers.anilibria_tv_parse)
            return await self._parse_page(service, url, mirrors, parse,
                                          transport="browser")

        raise SourceParseError(reason("tracker_no_impl", service=service))

    # --- браузерный пул (astar/anilibria_tv, Р-9): modules/sources/browser_pool.py ---------

    async def _browser_html(self, service: str, url: str) -> str:
        """HTML страницы трекера через браузер. Подготовка страницы
        зависит от трекера (порт старых парсеров): astar — открыть список
        кликом «Все торренты»; anilibria_tv — дождаться таблицы после
        JS-челленджа."""
        browser = await self.browsers.browser()
        context = await browser.new_context(
            user_agent=self._BROWSER_UA,
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True)
        try:
            page = await context.new_page()
            await page.goto(url, timeout=self.BROWSER_TIMEOUT,
                            wait_until="domcontentloaded")
            if service == "astar":
                await page.wait_for_selector("span#torrent_all",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
                await page.click("span#torrent_all")
                await page.wait_for_selector("div.list_torrent",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
            elif service == "anilibria_tv":
                await page.wait_for_selector("table#publicTorrentTable",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
            return await page.content()
        finally:
            await context.close()

    async def _parse_page(self, service: str, url: str,
                          mirrors: list[str], parse, *,
                          transport: str) -> dict:
        """Страница трекера + разбор. Разбор идёт ВНУТРИ попытки
        доставки, поэтому страница, которую не удалось разобрать,
        означает переезд на следующий хост, а не отказ. Результат несёт
        фактический URL ('url')."""
        delivered = await self._delivery.fetch(
            service, url, expect="page", mirrors=mirrors,
            transport=transport,
            accept=lambda html, attempt_url: parse(html, attempt_url))
        return {**delivered.value, "url": delivered.url}

    # --- VK API for the sites (finding 15: the token lives in trackerauth) -------------------

    async def _vk_call(self, method: str, params: dict) -> dict:
        """One call of the VK API through the delivery: the answer, or a `SiteError` with the reason the site reports."""
        try:
            delivered = await self._delivery.fetch(
                "vk", f"{VK_API_BASE}{method}", expect="json",
                transport="auth", params=params)
        except SourceParseError as exc:
            raise SiteError(reason("vk_call_failed", method=method, cause=str(exc))) from exc
        data = delivered.value
        if "error" in data:
            raise SiteError(reason("vk_api_error", method=method, message=data["error"].get("error_msg"),
                                   error_code=data["error"].get("error_code")))
        return data

    async def on_table_get(self, env: Envelope) -> dict:
        """payload: TableGetRequest; reply: TableGetReply. Never waits for a read: the end of it comes as an event."""
        reply = await self.tables.get(TableGetRequest.model_validate(env.payload))
        # The table of a page is thousands of rows: it is made into the message off the loop of the service.
        return await compute.run(reply.model_dump, mode="json")

    async def on_table_read(self, env: Envelope) -> None:
        await self.tables.read(TableGetRequest.model_validate(env.payload))

    async def on_table_cancel(self, env: Envelope) -> dict:
        """The read of a page stopped by the user (a sync stopped): it ends failed, the last table serves."""
        return {"cancelled": await self.tables.cancel(str(env.payload["url"]))}

    async def on_settings_get(self, env: Envelope) -> dict:
        return await self._settings()

    async def _settings(self) -> dict:
        stored = await self.repo.all_trackers()
        trackers = [TrackerMirrors(id=each["id"], display_name=each["display_name"], mirrors=list(each.get("mirrors") or []))
                    for each in stored]
        readers = [ParsedDomains(name=each.display_name, kind="tracker", domains=each.mirrors) for each in trackers]
        readers += [ParsedDomains(name=site.name, kind="site", domains=list(site.domains)) for site in self.sites]
        return SourcesSettingsView(trackers=trackers, readers=readers).model_dump(mode="json")

    async def on_site_resolve(self, env: Envelope) -> dict:
        """payload: SiteResolveRequest; reply: SiteResolveReply. No network."""
        request = SiteResolveRequest.model_validate(env.payload)
        return self.tables.resolve(request.url).model_dump(mode="json")

    async def on_players_list(self, env: Envelope) -> dict:
        """Every player a row of a table can be of, with the words a window names it by and where it is among the pages
        asked about: the players of the registry, and the admitted lines of the applications (`line:<code>`, named by
        their codes), each line in every application that admits it."""
        request = PlayersListRequest.model_validate(env.payload or {})
        urls = list(dict.fromkeys(self.tables.resolve(url).canonical_url or url for url in request.urls))
        pages, heights = await self.repo.players_of_pages(urls)

        def places(player: str, admitting: Iterable[AppPlayer] = ()) -> list[PlayerPlace]:
            found = {app.name: PlayerPlace(name=app.name, label=app.label, sources=pages.get((player, app.name), 0))
                     for app in admitting}
            labels = {app.name: app.label for app in self.lines.apps.values()}
            for (name, site), count in pages.items():
                if name == player and site not in found:
                    found[site] = PlayerPlace(name=site, label=labels.get(site, site), sources=count)
            return sorted(found.values(), key=lambda place: (-place.sources, place.label))

        players = [PlayerFacts(name=each.name, label=getattr(each, "label", "") or each.name, kind="site",
                               height=heights.get(each.name), places=places(each.name))
                   for each in self.players if not getattr(each, "lines", False)]
        for code in self.lines.codes():
            name = line_player_name(code)
            admitting = [app for app in self.lines.apps.values() if code in app.registry.admitted]
            players.append(PlayerFacts(name=name, label=code, kind="line", height=heights.get(name),
                                       places=places(name, admitting)))
        return PlayersListReply(players=players).model_dump(mode="json")

    async def on_apps_search(self, env: Envelope) -> dict:
        """payload: AppSearchRequest; reply: AppSearchReply — a page of the search of every application (or of the one
        named), each as the application gives it, or the reason it gave none."""
        request = AppSearchRequest.model_validate(env.payload)
        asked = [app for app in self.apps if not request.app or app.name == request.app]
        found = await asyncio.gather(*(self._search(app, request.query, request.page) for app in asked))
        return AppSearchReply(apps=list(found)).model_dump(mode="json")

    async def _search(self, app: AppPlayer, query: str, page: int) -> AppResults:
        try:
            answer = await app.client.search_page(query, page)
        except AppError as exc:
            return AppResults(app=app.name, label=app.label, page=page, reason=exc.reason)
        # A card without a number the application could be asked by again is shown all the same, with no address.
        cards = [AppCard(app=app.name, id=str(each.get("id", "")),
                         url=card_address(app.name, str(each["id"])) if str(each.get("id", "")).isdigit() else "",
                         name=str(each.get("name") or ""), sub=str(each.get("sub") or ""), year=str(each.get("year") or ""),
                         area=str(each.get("area") or ""), genres=str(each.get("class") or ""),
                         remarks=str(each.get("remarks") or ""), blurb=str(each.get("blurb") or "").strip(),
                         pic=str(each.get("pic") or ""))
                 for each in answer.cards]
        return AppResults(app=app.name, label=app.label, page=page, cards=cards, pages=answer.pages, total=answer.total)

    async def on_video_stream(self, env: Envelope) -> dict:
        """payload: {url, max_height?}
        reply: {handled, stream_url, headers, progressive, playlist, audio_url?} | {handled, error, transient}.
        `progressive` marks one ready file that a server may be asked for in parts; `playlist` an HLS playlist that is
        downloaded by its segments. `audio_url` pairs a complete video file with its separate audio file.

        `error` means the URL belongs to a player that has no stream for it:
        the caller must not hand the page to a generic downloader."""
        url, max_height = env.payload["url"], env.payload.get("max_height")
        try:
            stream_info = await resolve_stream(self.players, url, self._http, max_height)
        except StreamUnavailable as exc:
            return {"handled": True, "error": exc.reason,
                    "transient": exc.transient}
        if stream_info:
            return {
                "handled": True,
                "stream_url": stream_info["stream_url"],
                "headers": stream_info.get("headers", {}),
                "progressive": bool(stream_info.get("progressive")),
                "playlist": bool(stream_info.get("playlist")),
                "player_fetch": bool(stream_info.get("player_fetch")),
                **({"audio_url": stream_info["audio_url"]} if stream_info.get("audio_url") else {}),
            }
        return {"handled": False}

    async def on_video_fetch(self, env: Envelope) -> dict:
        """payload: {url, target, max_height?, connections}; reply: {ok, seconds, longest, height} | {ok: false, error,
        final}. The file of a link whose player downloads it itself (`player_fetch` of the stream): the player takes the
        segments into `target`, telling how far it is by `sources.video.progress {target, ...figures}`."""
        url, target = env.payload["url"], env.payload["target"]
        player = next((each for each in self.players if each.owns(url) and hasattr(each, "transfer")), None)
        if player is None:
            return {"ok": False, "error": reason("no_player_downloads"), "final": True}

        async def told(figures: dict) -> None:
            self.publish_event("sources.video.progress", {"target": target, **figures})

        work = asyncio.ensure_future(player.transfer(url, target, env.payload.get("max_height"),
                                                     int(env.payload.get("connections") or 1), told))
        self._transfers[target] = work
        try:
            done = await work
        except asyncio.CancelledError:
            if work.cancelled():
                return {"ok": False, "error": reason("download_stopped"), "final": False}
            raise
        except AllohaRefused as exc:
            self.log.warning("%s: загрузка плеером не удалась: %s", os.path.basename(target), describe(str(exc)))
            return {"ok": False, "error": str(exc), "final": exc.final}
        finally:
            self._transfers.pop(target, None)
        return {"ok": True, **done}

    async def on_video_fetch_stop(self, env: Envelope) -> dict:
        """payload: {target}; the download of the player into that file stops (its owner gave the file up)."""
        work = self._transfers.get(env.payload["target"])
        if work is not None:
            work.cancel()
        return {"stopped": work is not None}

    # --- скачивание .torrent (Р-1) ----------------------------------------------------

    async def on_torrent_file(self, env: Envelope) -> dict:
        url, torrent_id = env.payload["url"], env.payload["torrent_id"]
        cached = await storage.call(self._cache_read, torrent_id)
        if cached is not None:
            return {"content_b64": base64.b64encode(cached).decode(),
                    "from_cache": True}

        tracker = await self.repo.resolve(url)
        service = tracker["canonical_name"] if tracker else ""
        mirrors = tracker["mirrors"] if tracker else []
        delivered = await self._delivery.fetch(
            service, url, expect="torrent", mirrors=mirrors,
            transport="auth" if service in ("kinozal", "rutracker") else "http",
            timeout=delivery.TORRENT_TIMEOUT)
        content = delivered.value

        await storage.call(self._cache_write, torrent_id, content, owns=self._cache_path(torrent_id))
        return {"content_b64": base64.b64encode(content).decode(),
                "from_cache": False}

    # --- кэш .torrent-файлов ------------------------------------------------------------

    def _cache_path(self, torrent_id: str) -> str:
        import os
        return os.path.join(self._cache_dir, f"{torrent_id}.torrent")

    def _cache_read(self, torrent_id: str) -> bytes | None:
        import os
        path = self._cache_path(torrent_id)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        return None

    def _cache_write(self, torrent_id: str, content: bytes) -> None:
        import os
        os.makedirs(self._cache_dir, exist_ok=True)
        with open(self._cache_path(torrent_id), "wb") as f:
            f.write(content)
