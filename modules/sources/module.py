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
  sources.site.resolve {url} → {site, canonical_url}: which site reads the address and the canonical
                                   form of it (no network); both null when no site does
  sources.settings.get {} → SourcesSettingsView {trackers: [{id, display_name, mirrors}], readers: [{name, kind:
                                   tracker | site, domains}]}: the settings screen of the sources whole — the mirrors of
                                   each tracker, and the domains the service reads pages of now: every tracker with its
                                   domains and mirrors, every site of the registry with its own (no network)
  sources.video.stream {url, max_height?} → {handled, stream_url, headers, progressive, playlist} | {handled, error,
                                   transient}: the stream of a candidate link, asked for right before the download.
                                   The player that owns the link (a VK player link with its key, an AllVideo embed,
                                   a Vibix entry, an item of CDN VideoHub or of Kodik) resolves it; any other
                                   address is not handled
  sources.players.list {} → {players: [{name, label}]}: every player a row of a table can be of, as the order of sources
                                   names it: the players of the registry and the admitted lines of the applications
                                   (`<application>:<code>`, named by their codes; modules/sources/apps/lines.py)
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
Events in: settings.changed {key: sources_term_hours} — the term of what the sources answered (term.py): the tables of
pages, the listings of VK channels and the addresses parsers of applications gave are current for that many hours.
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

import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Callable, Iterable

import httpx

from core import BaseModule, compute, ids
from core import storage
from core.contracts import (AppCard, AppResults, AppSearchReply, AppSearchRequest, ParsedDomains, SiteResolveRequest,
                            SourcesSettingsView, TableGetRequest, TrackerMirrors)
from core.deadlines import TABLE_READ_S
from core.db import Database
from core.envelope import Envelope
from core.http_limits import bounded_client
from core.reasons import reason

from . import anilibria, delivery, parsers
from .apps import AppProfile, card_address
from .apps.app99 import AppError, App99Client
from .apps.lines import LineRegistry
from .delivery import Delivery
from .parsers import SourceParseError
from .repository import SourcesRepository
from .term import DEFAULT_HOURS, TERM_KEY, Term, hours_of
from .players.app_lines import AppPlayer
from .players.contract import StreamUnavailable
from .players.registry import PlayerRegistry
from .players.streams import resolve_stream
from .sites import Site, SiteError, SiteRegistry
from .sites.apps import AppSite
from .sites.apps.site import ROWS_IN_FLIGHT
from .sites.builtin import builtin_sites
from .table import TableReader, utc_now

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру
VK_API_BASE = "https://api.vk.com/method/"


# The connections the client of the sources opens and keeps open between requests. A read of a card of an application
# has up to ROWS_IN_FLIGHT rows going, each one request at a time: kept as many, a row asks over an open connection
# instead of a new TCP and TLS handshake (measured on 半日: a row 5.2 s with the 20 httpx keeps, 2.8 s with 100), and as
# many again are left to the reads of the other sources going at the same time.
CONNECTIONS = httpx.Limits(max_connections=2 * ROWS_IN_FLIGHT, max_keepalive_connections=2 * ROWS_IN_FLIGHT,
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
                 apps: Iterable[AppProfile] = (), app_lines_dir: str | None = None) -> None:
        self.repo = SourcesRepository(db)
        self._cache_dir = torrent_cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        wiring = dict(
            browser_getter=self._ensure_browser,
            key_path=os.path.abspath(os.path.join(torrent_cache_dir,
                                                  "vibix-key.json")),
            manifest_dir=os.path.abspath(os.path.join(torrent_cache_dir,
                                                      "manifests")))
        # One object per player for the whole module: the sites read with them and the streams are resolved by them.
        self.players = PlayerRegistry.standard(**wiring)
        # The video applications the environment describes: each a player of its lines and a site of its cards.
        self.apps: list[AppPlayer] = []
        # One term of what the sources answered for every reader of the module; the setting changes it for all at once.
        self.term = Term()
        for profile in apps:
            if profile.protocol != "app99":
                raise ValueError(f"application {profile.name}: unknown protocol {profile.protocol!r}")
            player = AppPlayer(App99Client(profile, lambda: self._http),
                               registry=LineRegistry.load(profile.name, app_lines_dir), term=self.term)
            self.players.register(player)
            self.apps.append(player)
        # The sites belong to this instance, not to the process: a site is added by registering it (builtin.py).
        self.sites = SiteRegistry([*(builtin_sites() if sites is None else sites), *(AppSite(app) for app in self.apps)])
        self.sites.configure(players=self.players, vk_call=self._vk_call, term=self.term)
        self.tables = TableReader(
            self.repo, self.sites, lambda topic, payload: self.publish_event(topic, payload), lambda: self._http,
            players=self.players, clock=table_clock, safeguard_s=table_safeguard_s, recent=read_recent, term=self.term)
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
        # Браузерный пул (astar/anilibria_tv) поднимается ЛЕНИВО при
        # первом обращении — на стенде без этих трекеров firefox не
        # запускается (находка 14: один браузер на модуль, а не на скан).
        self._pw = None
        self._browser = None
        try:
            stored = (await self.request("settings.value.get", {"key": TERM_KEY})).get("value")
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves the term at its default
            stored = None
        self._set_term(stored)
        try:
            await self.tables.resume()
        except Exception:  # noqa: BLE001 — the other duties of the module do not wait for the table reads
            # Consumers ask for their tables again when they start; a page left marked "reading" is read at the next ask.
            self.log.exception("reads of tables interrupted by a stop were not resumed")

    async def on_stop(self) -> None:
        await self.tables.close()
        await self._http.aclose()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

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
            raise SourceParseError(f"трекер не распознан по URL: {url}")
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

        raise SourceParseError(f"нет реализации для трекера {service}")

    # --- браузерный пул (astar/anilibria_tv, Р-9) ------------------------------------

    async def _ensure_browser(self):
        """Ленивый запуск общего браузера: поднимается при первом
        обращении к astar/anilibria_tv, переиспользуется (контекст — на
        запрос). Снимает находку 14 (старый код плодил браузер на скан)."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.firefox.launch(headless=True)
            self.log.info("браузерный пул запущен (firefox, headless)")
        return self._browser

    async def _browser_html(self, service: str, url: str) -> str:
        """HTML страницы трекера через браузер. Подготовка страницы
        зависит от трекера (порт старых парсеров): astar — открыть список
        кликом «Все торренты»; anilibria_tv — дождаться таблицы после
        JS-челленджа."""
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=self._BROWSER_UA,
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True)
        page = await context.new_page()
        try:
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
        """Every player a row of a table can be of, with the words a window names it by: the players of the registry, and
        the admitted lines of the applications (`<application>:<code>`, named by their codes)."""
        players = [{"name": each.name, "label": getattr(each, "label", "") or each.name}
                   for each in self.players if not getattr(each, "lines", False)]
        players += [{"name": line.name, "label": line.label} for app in self.apps for line in app.line_players()]
        return {"players": players}

    async def on_apps_search(self, env: Envelope) -> dict:
        """payload: AppSearchRequest; reply: AppSearchReply — a page of the search of every application (or of the one
        named), each as the application gives it, or the reason it gave none."""
        request = AppSearchRequest.model_validate(env.payload)
        asked = [app for app in self.apps if not request.app or app.name == request.app]
        found = await asyncio.gather(*(self._search(app, request.query, request.page) for app in asked))
        return AppSearchReply(apps=list(found)).model_dump(mode="json")

    async def _search(self, app: AppPlayer, query: str, page: int) -> AppResults:
        try:
            answer = await app.client.search(query, page)
        except AppError as exc:
            return AppResults(app=app.name, label=app.label, page=page, reason=exc.reason)
        # A card without a number the application could be asked by again is shown all the same, with no address.
        cards = [AppCard(app=app.name, id=str(each.get("id", "")),
                         url=card_address(app.name, str(each["id"])) if str(each.get("id", "")).isdigit() else "",
                         name=str(each.get("name") or ""), sub=str(each.get("sub") or ""), year=str(each.get("year") or ""),
                         area=str(each.get("area") or ""), genres=str(each.get("class") or ""),
                         remarks=str(each.get("remarks") or ""), blurb=str(each.get("blurb") or "").strip(),
                         pic=str(each.get("pic") or ""))
                 for each in answer.get("data") or [] if isinstance(each, dict)]
        pages, total = answer.get("page_count"), answer.get("total_count", answer.get("count"))
        return AppResults(app=app.name, label=app.label, page=page, cards=cards,
                          pages=int(pages) if str(pages).isdigit() else None, total=int(total) if str(total).isdigit() else None)

    async def on_video_stream(self, env: Envelope) -> dict:
        """payload: {url, max_height?}
        reply: {handled, stream_url, headers, progressive, playlist} | {handled, error, transient}.
        `progressive` marks one ready file that a server may be asked for in parts; `playlist` an HLS playlist that is
        downloaded by its segments.

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
            }
        return {"handled": False}

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
