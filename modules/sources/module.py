"""Модуль sources — источники раздач: парсеры трекеров (Р-9).

Queries:
  sources.parse {url}            → {service, title, releases[]}
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.torrent_file.drop {torrent_id} — чистка кэша (каскад Р-19)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)
  sources.tracker.resolve {url}  → {tracker|null} (tracker_info деталей)

Доставка страниц:
  kinozal / rutracker — query trackerauth.fetch (сессии — не наша забота);
  anilibria — официальный API напрямую (httpx, авторизация не нужна);
  astar / anilibria_tv — браузерная (Playwright-пул), подключается на
  этапе 6 при установке браузеров на стенд; функции разбора готовы.

Между запросами к одному трекеру выдерживается пауза (уважение к
чужому сервису) — обеспечивается замком на сервис + минимальным
интервалом.
"""
from __future__ import annotations

import asyncio
import base64
import time

import httpx

from core import BaseModule, ids
from core.db import Database
from core.envelope import Envelope

from . import anilibria, parsers, vk
from .parsers import SourceParseError
from .repository import SourcesRepository

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру


class SourcesModule(BaseModule):
    name = "sources"

    def __init__(self, bus, db: Database, *,
                 torrent_cache_dir: str = "torrent_cache",
                 vk_page_interval: float = 1.0) -> None:
        self.repo = SourcesRepository(db)
        self._cache_dir = torrent_cache_dir
        self.vk_page_interval = vk_page_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("sources.parse", self.on_parse)
        self.handle("sources.torrent_file.get", self.on_torrent_file)
        self.handle("sources.torrent_file.drop", self.on_torrent_file_drop)
        self.handle("sources.trackers.list", self.on_trackers_list)
        self.handle("sources.tracker.resolve", self.on_tracker_resolve)
        self.handle("sources.tracker.set_mirrors", self.on_set_mirrors)
        self.handle("sources.vk.scan", self.on_vk_scan)

    async def on_start(self) -> None:
        self._http = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    async def on_stop(self) -> None:
        await self._http.aclose()

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
        await asyncio.to_thread(
            lambda: os.path.isfile(path) and os.remove(path))

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
            resp = await self._http.get(f"{api}/anime/releases/{alias}")
            resp.raise_for_status()
            return anilibria.release_to_result(resp.json())

        if service in ("kinozal", "rutracker"):
            parse = (parsers.kinozal_parse if service == "kinozal"
                     else parsers.rutracker_parse)
            return await self._parse_with_mirrors(service, url, mirrors,
                                                  parse)

        if service in ("astar", "anilibria_tv"):
            # Функции разбора готовы (parsers.astar_parse /
            # anilibria_tv_parse); браузерная доставка — этап 6.
            raise SourceParseError(
                f"{service}: браузерная доставка страниц подключается на "
                "этапе 6 (Playwright на стенде); см. revision.md Р-9")

        raise SourceParseError(f"нет реализации для трекера {service}")

    async def _parse_with_mirrors(self, service: str, url: str,
                                  mirrors: list[str], parse) -> dict:
        """Фоллбэк зеркал живёт здесь, а не в scan: владелец таблицы
        trackers и доставки страниц — sources (Р-7). Пробуем исходный
        URL, затем остальные зеркала; неудача всех — громкая ошибка со
        списком попыток. Результат несёт фактический URL ('url')."""
        from urllib.parse import urlparse
        original = urlparse(url)
        candidates = [url] + [original._replace(netloc=m).geturl()
                              for m in mirrors if m and m != original.netloc]
        errors = []
        for attempt_url in candidates:
            try:
                reply = await self.request("trackerauth.fetch", {
                    "service": service, "url": attempt_url, "timeout": 20},
                    timeout=60)
                if reply["status"] != 200:
                    raise SourceParseError(f"HTTP {reply['status']}")
                result = parse(reply["text"], attempt_url)
                result["url"] = attempt_url
                if attempt_url != url:
                    self.log.info("%s: основной URL не сработал, страница "
                                  "получена с зеркала %s", service,
                                  urlparse(attempt_url).netloc)
                return result
            except Exception as exc:  # сеть, HTTP, разбор — пробуем дальше
                errors.append(f"{urlparse(attempt_url).netloc}: {exc}")
                self.log.warning("%s: зеркало не сработало — %s", service,
                                 errors[-1])
        raise SourceParseError(
            f"{service}: не сработало ни одно зеркало ({'; '.join(errors)})")

    # --- VK (находка 15: официальный API, токен живёт в trackerauth) -------------------

    async def _vk_call(self, method: str, params: dict) -> dict:
        import json as _json
        reply = await self.request("trackerauth.fetch", {
            "service": "vk", "url": f"{vk.API_BASE}{method}",
            "params": params, "timeout": 20}, timeout=60)
        try:
            data = _json.loads(reply.get("text") or "")
        except _json.JSONDecodeError as exc:
            raise vk.VkScanError(f"VK {method}: не-JSON ответ") from exc
        if "error" in data:
            raise vk.VkScanError(
                f"VK {method}: {data['error'].get('error_msg')} "
                f"(код {data['error'].get('error_code')})")
        return data

    async def _vk_paginate(self, method: str, params: dict,
                           *, max_pages: int = 50) -> list[dict]:
        items, offset = [], 0
        for page in range(max_pages):
            data = await self._vk_call(method, {
                **params, "offset": offset, "count": vk.PAGE_SIZE})
            page_items = (data.get("response") or {}).get("items", [])
            if not page_items:
                break
            items.extend(page_items)
            offset += vk.PAGE_SIZE
            await asyncio.sleep(self.vk_page_interval)  # пауза — уважение к API
        else:
            self.log.warning("VK %s: достигнут предел %d страниц", method,
                             max_pages)
        return items

    async def on_vk_scan(self, env: Envelope) -> dict:
        """payload: {channel_url, query, search_mode: 'search'|'get_all'}
        reply:   {videos: [{title, url, publication_date, resolution}]}"""
        p = env.payload
        screen_name = vk.screen_name_from_url(p["channel_url"])
        resolve = await self._vk_call("utils.resolveScreenName",
                                      {"screen_name": screen_name})
        owner_id = vk.owner_id_from_resolve(resolve)
        base = {"owner_id": owner_id, "fields": "files"}
        terms = [t.strip() for t in (p.get("query") or "").split("/")
                 if t.strip()]

        if p.get("search_mode", "search") == "search":
            raw: list[dict] = []
            for term in terms:
                raw.extend(await self._vk_paginate("video.search",
                                                   {**base, "q": term}))
            raw = vk.dedupe_by_id(raw)
        else:  # get_all: весь канал + локальный фильтр
            raw = vk.filter_by_terms(
                await self._vk_paginate("video.get", base), terms)

        facts = [f for f in (vk.video_to_fact(v) for v in raw) if f]
        facts.sort(key=lambda f: f["publication_date"], reverse=True)
        self.log.info("VK-скан %s: видео %d (сырых %d)", screen_name,
                      len(facts), len(raw))
        return {"videos": facts}

    # --- скачивание .torrent (Р-1) ----------------------------------------------------

    async def on_torrent_file(self, env: Envelope) -> dict:
        url, torrent_id = env.payload["url"], env.payload["torrent_id"]
        cached = await asyncio.to_thread(self._cache_read, torrent_id)
        if cached is not None:
            return {"content_b64": base64.b64encode(cached).decode(),
                    "from_cache": True}

        tracker = await self.repo.resolve(url)
        service = tracker["canonical_name"] if tracker else None
        if service in ("kinozal", "rutracker"):
            reply = await self.request("trackerauth.fetch", {
                "service": service, "url": url, "timeout": 30}, timeout=90)
            if reply["status"] != 200:
                raise SourceParseError(f"скачивание {url}: HTTP {reply['status']}")
            if "text" in reply:  # трекер вернул HTML вместо файла
                self._raise_html_error(reply["text"], url)
            content = base64.b64decode(reply["content_b64"])
        else:
            resp = await self._http.get(url)
            resp.raise_for_status()
            if "text/html" in resp.headers.get("content-type", ""):
                self._raise_html_error(resp.text, url)
            content = resp.content

        await asyncio.to_thread(self._cache_write, torrent_id, content)
        return {"content_b64": base64.b64encode(content).decode(),
                "from_cache": False}

    @staticmethod
    def _raise_html_error(text: str, url: str) -> None:
        # Известные причины из разбора старого qbittorrent.py
        if "Вы использовали доступное Вам количество торрент-файлов" in text:
            raise SourceParseError(
                f"{url}: достигнут суточный лимит скачиваний на трекере")
        if "Вам необходимо включить JavaScript" in text:
            raise SourceParseError(f"{url}: трекер требует JavaScript")
        raise SourceParseError(f"{url}: трекер вернул HTML вместо .torrent")

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
