"""Модуль sources — источники раздач: парсеры трекеров (Р-9).

Queries:
  sources.parse {url}            → {service, title, releases[]}
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)

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

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from . import anilibria, parsers
from .parsers import SourceParseError
from .repository import SourcesRepository

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру


class SourcesModule(BaseModule):
    name = "sources"

    def __init__(self, bus, db: Database, *,
                 torrent_cache_dir: str = "torrent_cache") -> None:
        self.repo = SourcesRepository(db)
        self._cache_dir = torrent_cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("sources.parse", self.on_parse)
        self.handle("sources.torrent_file.get", self.on_torrent_file)
        self.handle("sources.trackers.list", self.on_trackers_list)

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

    async def on_parse(self, env: Envelope) -> dict:
        url = env.payload["url"]
        tracker = await self.repo.resolve(url)
        if not tracker:
            raise SourceParseError(f"трекер не распознан по URL: {url}")
        service = tracker["canonical_name"]
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            await self._polite(service)
            result = await self._parse_by_service(service, url)
        result["service"] = service
        result["ui_features"] = tracker["ui_features"]
        return result

    async def _parse_by_service(self, service: str, url: str) -> dict:
        if service == "anilibria":
            alias = anilibria.alias_from_url(url)
            api = anilibria.api_base_from_url(url)
            resp = await self._http.get(f"{api}/anime/releases/{alias}")
            resp.raise_for_status()
            return anilibria.release_to_result(resp.json())

        if service in ("kinozal", "rutracker"):
            reply = await self.request("trackerauth.fetch", {
                "service": service, "url": url, "timeout": 20}, timeout=60)
            if reply["status"] != 200:
                raise SourceParseError(
                    f"{service}: HTTP {reply['status']} на {url}")
            parse = (parsers.kinozal_parse if service == "kinozal"
                     else parsers.rutracker_parse)
            return parse(reply["text"], url)

        if service in ("astar", "anilibria_tv"):
            # Функции разбора готовы (parsers.astar_parse /
            # anilibria_tv_parse); браузерная доставка — этап 6.
            raise SourceParseError(
                f"{service}: браузерная доставка страниц подключается на "
                "этапе 6 (Playwright на стенде); см. revision.md Р-9")

        raise SourceParseError(f"нет реализации для трекера {service}")

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
