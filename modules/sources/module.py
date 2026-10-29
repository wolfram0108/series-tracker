"""Модуль sources — источники раздач: парсеры трекеров (Р-9).

Queries:
  sources.parse {url}            → {service, title, releases[]}
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.torrent_file.drop {torrent_id} — чистка кэша (каскад Р-19)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)
  sources.tracker.resolve {url}  → {tracker|null} (tracker_info деталей)
  sources.provider.info {url}    → {provider|null, voices} — провайдер веб-видео по адресу и
                                   есть ли у него каталог озвучек (без обращения в сеть)

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

import httpx
from pydantic import ValidationError

from core import BaseModule, ids
from core import storage
from core.contracts import Release
from core.db import Database
from core.envelope import Envelope

from . import anilibria, delivery, parsers, vk
from .delivery import Delivery
from .parsers import SourceParseError
from .repository import SourcesRepository
from .video_providers import (StreamUnavailable, VideoProviderRegistry,
                              get_default_registry)

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру


class SourcesModule(BaseModule):
    name = "sources"

    # Браузерная доставка astar/anilibria_tv (Р-9). UA/viewport — как в
    # старом firefox-парсере (защита трекеров фингерпринтит браузер).
    _BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                   "Gecko/20100101 Firefox/125.0")
    BROWSER_TIMEOUT = 30000  # мс на загрузку/JS-челлендж в браузере

    def __init__(self, bus, db: Database, *,
                 torrent_cache_dir: str = "torrent_cache",
                 vk_page_interval: float = 1.0) -> None:
        self.repo = SourcesRepository(db)
        self._cache_dir = torrent_cache_dir
        self.vk_page_interval = vk_page_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self.video_registry = get_default_registry()
        self.video_registry.configure(
            browser_getter=self._ensure_browser,
            key_path=os.path.abspath(os.path.join(torrent_cache_dir,
                                                  "vibix-key.json")),
            manifest_dir=os.path.abspath(os.path.join(torrent_cache_dir,
                                                      "manifests")))
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
        self.handle("sources.vk.scan", self.on_vk_scan)
        self.handle("sources.video.scan", self.on_video_scan)
        self.handle("sources.video.stream", self.on_video_stream)
        self.handle("sources.provider.info", self.on_provider_info)

    async def on_start(self) -> None:
        self._http = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Браузерный пул (astar/anilibria_tv) поднимается ЛЕНИВО при
        # первом обращении — на стенде без этих трекеров firefox не
        # запускается (находка 14: один браузер на модуль, а не на скан).
        self._pw = None
        self._browser = None

    async def on_stop(self) -> None:
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
        provider = self.video_registry.find_provider(url)
        if provider:
            service = provider.name
            lock = self._locks.setdefault(service, asyncio.Lock())
            async with lock:
                await self._polite(service)
                parsed = await provider.parse_release(url, self._http)
            try:
                release = Release.model_validate(parsed)
            except ValidationError as exc:
                self.log.error("провайдер %s вернул разбор не по контракту: %s", service, exc)
                raise ValueError(f"{service}: разбор страницы не соответствует контракту провайдера") from exc
            sources = [source.model_dump(mode="json", exclude_unset=True) for source in release.sources]
            if not env.payload.get("with_links"):
                # Links of every episode are for the voices module; the form does not need them.
                sources = [{k: v for k, v in source.items() if k != "links"} for source in sources]
            return {
                "service": service,
                "source_type": "web_video",
                "title": parsed["title"],
                "titles": parsed.get("titles", []),
                "episodes": parsed.get("episodes", []),
                "resolution": parsed.get("resolution"),
                "videos": parsed.get("videos", []),
                "releases": parsed.get("releases", []),
                "canonical_url": parsed.get("canonical_url", url),
                "episodes_count": parsed.get("episodes_count", 0),
                "players": parsed.get("players", {}),
                "sources": sources,
                "selected_source": parsed.get("selected_source"),
                "ui_features": {},
                "tracker": None,
            }

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

    # --- VK (находка 15: официальный API, токен живёт в trackerauth) -------------------

    async def _vk_call(self, method: str, params: dict) -> dict:
        try:
            delivered = await self._delivery.fetch(
                "vk", f"{vk.API_BASE}{method}", expect="json",
                transport="auth", params=params, bus_timeout=60)
        except SourceParseError as exc:
            raise vk.VkScanError(f"VK {method}: {exc}") from exc
        data = delivered.value
        if "error" in data:
            raise vk.VkScanError(
                f"VK {method}: {data['error'].get('error_msg')} "
                f"(код {data['error'].get('error_code')})")
        return data

    async def _vk_paginate(self, method: str, params: dict,
                           *, max_pages: int = 50, progress=None) -> list[dict]:
        items, offset, total = [], 0, 0
        for page in range(max_pages):
            data = await self._vk_call(method, {
                **params, "offset": offset, "count": vk.PAGE_SIZE})
            response = data.get("response") or {}
            page_items = response.get("items", [])
            if page == 0:
                total = int(response.get("count") or 0)
            if not page_items:
                break
            items.extend(page_items)
            offset += vk.PAGE_SIZE
            if progress is not None:
                progress(len(items), max(total, len(items)), "videos")
            await asyncio.sleep(self.vk_page_interval)  # pause respects the API
        else:
            self.log.warning("VK %s: достигнут предел %d страниц", method,
                             max_pages)
        return items

    async def on_vk_scan(self, env: Envelope) -> dict:
        """payload: {channel_url, query, search_mode: 'search'|'get_all'}
        reply:   {videos: [{title, url, publication_date, resolution}]}"""
        p = env.payload
        series_id = p.get("series_id")
        started = time.monotonic()
        state = {"current": 0, "total": 0}

        def report(current: int, total: int, phase: str) -> None:
            state["current"] = current
            state["total"] = total
            elapsed = time.monotonic() - started
            self.publish_event("sources.video.progress", {
                "series_id": series_id, "current": current,
                "total": total,
                "rate": round(elapsed / current, 2) if current else 0.0,
                "phase": phase, "active": phase not in ("done", "error"),
                "kind": "videos"})

        screen_name = vk.screen_name_from_url(p["channel_url"])
        resolve = await self._vk_call("utils.resolveScreenName",
                                      {"screen_name": screen_name})
        owner_id = vk.owner_id_from_resolve(resolve)
        base = {"owner_id": owner_id, "fields": "files"}
        terms = [t.strip() for t in (p.get("query") or "").split("/")
                 if t.strip()]

        try:
            if p.get("search_mode", "search") == "search":
                raw: list[dict] = []
                for term in terms:
                    raw.extend(await self._vk_paginate(
                        "video.search", {**base, "q": term},
                        progress=report))
                raw = vk.dedupe_by_id(raw)
            else:  # get_all: весь канал + локальный фильтр
                raw = vk.filter_by_terms(
                    await self._vk_paginate("video.get", base,
                                            progress=report), terms)
        except Exception:
            report(state["current"], state["total"], "error")
            raise

        facts = [f for f in (vk.video_to_fact(v) for v in raw) if f]
        facts.sort(key=lambda f: f["publication_date"], reverse=True)
        report(state["current"], state["total"], "done")
        self.log.info("VK-скан %s: видео %d (сырых %d)", screen_name,
                      len(facts), len(raw))
        return {"videos": facts}

    async def on_video_scan(self, env: Envelope) -> dict:
        """payload: {url, series: dict}
        reply:   {videos: [{title, url, publication_date, resolution}],
                  players: {name: {present, available, selected, ...}}}"""
        url = env.payload["url"]
        provider = self.video_registry.find_provider(url)
        if not provider:
            raise SourceParseError(f"видео-провайдер не найден по URL: {url}")
        service = provider.name
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            await self._polite(service)
            series = env.payload.get("series") or {}
            series_id = series.get("id")
            started = time.monotonic()
            state = {"current": 0, "total": 0}

            def report(current: int, total: int, phase: str) -> None:
                state["current"] = current
                state["total"] = total
                elapsed = time.monotonic() - started
                self.publish_event("sources.video.progress", {
                    "series_id": series_id, "current": current,
                    "total": total,
                    "rate": round(elapsed / current, 2) if current else 0.0,
                    "phase": phase, "active": phase != "done",
                    "kind": "episodes"})

            try:
                result = await provider.scan_episodes(
                    url, self._http, series,
                    episodes=env.payload.get("episodes"), progress=report)
            except Exception:
                self.publish_event("sources.video.progress", {
                    "series_id": series_id, "current": state["current"],
                    "total": state["total"], "rate": 0.0,
                    "phase": "error", "active": False,
                    "kind": "episodes"})
                raise
        videos = result.get("videos", []) if isinstance(result, dict) else result
        players = result.get("players", {}) if isinstance(result, dict) else {}
        self.log.info("Видео-скан %s (%s): серий %d", service, url, len(videos))
        return {"videos": videos, "players": players}

    async def on_provider_info(self, env: Envelope) -> dict:
        """payload: {url}; reply: {provider: name | None, voices: bool}. No network: what the
        provider declares about itself, so that callers do not parse a page to find out."""
        provider = self.video_registry.find_provider(env.payload["url"])
        return {"provider": provider.name if provider else None,
                "voices": bool(provider and provider.has_voice_catalog)}

    async def on_video_stream(self, env: Envelope) -> dict:
        """payload: {url, max_height?}
        reply: {handled, stream_url, headers} | {handled, error, transient}.

        `error` means the URL belongs to a provider that has no stream for it:
        the caller must not hand the page to a generic downloader."""
        url = env.payload["url"]
        try:
            stream_info = await self.video_registry.resolve_stream(
                url, self._http, env.payload.get("max_height"))
        except StreamUnavailable as exc:
            return {"handled": True, "error": exc.reason,
                    "transient": exc.transient}
        if stream_info:
            return {
                "handled": True,
                "stream_url": stream_info["stream_url"],
                "headers": stream_info.get("headers", {}),
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
