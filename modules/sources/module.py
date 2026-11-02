"""Модуль sources — источники раздач: парсеры трекеров (Р-9).

Queries:
  sources.parse {url}            → {service, title, releases[]} — the releases of a tracker; a page of a site
                                   (ruchime, dunhuatv, a VK channel) is read by `sources.table.get`, not parsed
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.torrent_file.drop {torrent_id} — чистка кэша (каскад Р-19)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)
  sources.tracker.resolve {url}  → {tracker|null} (tracker_info деталей)

Tables of site pages (the one way to read a site page; sites are registered in `sites`, the core is `table.py`):
  sources.table.get {url, options?, refresh?} → TableGetReply {url, state: ready | reading | failed | absent,
                                   table?, reason?}. `options` are the choices of a source that change what is
                                   read (`vk_search_mode`); a site ignores those it does not know. A complete
                                   table younger than an hour is `ready`. Otherwise the page is read — by one
                                   task however many ask — and the reply is `reading` at once, with the last
                                   complete table when there is one; the end comes as the events below.
                                   `failed` in the reply means no read began (`site_not_found`: no site for the
                                   address). `url` of the reply is the canonical address the site gives: the key
                                   of the table and the `url` of the events. `refresh` false reads nothing and
                                   says how the page stands: `reading` while a read goes, `failed` (with the
                                   reason) when the last read failed, `ready` for a complete table however old,
                                   `absent` when nothing was read; with the last complete table when there is one.
  sources.table.read {url, options?} command: read the page now, term or not, unless a read of it is going
  sources.site.resolve {url, options?} → {site, canonical_url}: which site reads the address and the canonical
                                   form of it (no network); both null when no site does
  sources.video.stream {url, max_height?} → {handled, stream_url, headers, progressive} | {handled, error,
                                   transient}: the stream of a candidate link, asked for right before the download.
                                   The player that owns the link (a VK player link with its key, an AllVideo embed,
                                   a Vibix entry, an item of CDN VideoHub or of Kodik) resolves it; any other
                                   address is not handled
Events (owner: sources):
  sources.table.progress {url, phase, current, total}   how far a read is
  sources.table.ready {url, read_at}                    a complete table is stored and served
  sources.table.failed {url, reason}                    the read failed; the last complete table still serves
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

from core import BaseModule, ids
from core import storage
from core.contracts import SiteResolveRequest, TableGetRequest
from core.deadlines import TABLE_READ_S
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason

from . import anilibria, delivery, parsers
from .delivery import Delivery
from .parsers import SourceParseError
from .repository import SourcesRepository
from .players.contract import StreamUnavailable
from .players.registry import PlayerRegistry
from .players.streams import resolve_stream
from .sites import Site, SiteError, SiteRegistry
from .sites.builtin import builtin_sites
from .table import TableReader, utc_now

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру
VK_API_BASE = "https://api.vk.com/method/"


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
                 table_safeguard_s: float = TABLE_READ_S, read_recent: int | None = None) -> None:
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
        # The sites belong to this instance, not to the process: a site is added by registering it (builtin.py).
        self.sites = SiteRegistry(builtin_sites() if sites is None else sites)
        self.sites.configure(players=self.players, vk_call=self._vk_call)
        self.tables = TableReader(
            self.repo, self.sites, lambda topic, payload: self.publish_event(topic, payload), lambda: self._http,
            clock=table_clock, safeguard_s=table_safeguard_s, recent=read_recent)
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
        self.handle("sources.site.resolve", self.on_site_resolve)

    async def on_start(self) -> None:
        self._http = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Браузерный пул (astar/anilibria_tv) поднимается ЛЕНИВО при
        # первом обращении — на стенде без этих трекеров firefox не
        # запускается (находка 14: один браузер на модуль, а не на скан).
        self._pw = None
        self._browser = None
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
        return reply.model_dump(mode="json")

    async def on_table_read(self, env: Envelope) -> None:
        await self.tables.read(TableGetRequest.model_validate(env.payload))

    async def on_site_resolve(self, env: Envelope) -> dict:
        """payload: SiteResolveRequest; reply: SiteResolveReply. No network."""
        request = SiteResolveRequest.model_validate(env.payload)
        return self.tables.resolve(request.url, request.options).model_dump(mode="json")

    async def on_video_stream(self, env: Envelope) -> dict:
        """payload: {url, max_height?}
        reply: {handled, stream_url, headers, progressive} | {handled, error, transient}.
        `progressive` marks one ready file that a server may be asked for in parts.

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
