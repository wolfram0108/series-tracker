"""Единственный выход sources наружу: доставка с перебором хостов.

Любое обращение к источнику идёт через `Delivery.fetch`, поэтому путь,
забывший про переезд на живой хост или про проверку того, ЧТО приехало,
в модуле не заводится (находка 68). Раньше инвариант «умей переехать»
был только у разбора страниц, а скачивание .torrent и API anilibria
ходили по хосту из ссылки — мёртвый хост означал отказ конвейера.

Транспорты: `auth` — через trackerauth (сессии, антибот-челлендж),
`http` — прямой httpx, `browser` — общий Playwright-пул.

Ожидаемый вид результата объявляется явно (`expect`: page | torrent |
json) и проверяется детерминированно: заглушка сайта с кодом 200
ответом не считается. Попытка удачна, когда результат доставлен И
принят — доменная проверка (`accept`) выполняется ВНУТРИ попытки,
поэтому неразобранная страница ведёт к переезду на следующий хост, а не
к отказу.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from . import hosts, parsers
from .parsers import SourceParseError, SourceRefusedError

TORRENT_TIMEOUT = 30       # секунд на запрос .torrent у трекера
PAGE_TIMEOUT = 20          # секунд на запрос страницы
# Bus budget covers an anti-bot challenge solved by a browser (up to 45 s).
BUS_TIMEOUT = 150


@dataclass(frozen=True)
class Delivered:
    """Что доехало и с какого URL (фактический хост — часть контракта)."""
    url: str
    value: Any


def _accept_page(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        raise SourceParseError("ожидалась страница, приехал файл")
    return raw


def _accept_torrent(raw: str | bytes) -> bytes:
    if isinstance(raw, str):
        parsers.raise_not_a_torrent(raw)
    # Metainfo is a bencoded dict with an 'info' key — the only answer
    # that proves a file arrived rather than the site's own page.
    if not (raw.startswith(b"d") and b"4:info" in raw):
        raise SourceParseError("ответ не является .torrent (bencode)")
    return raw


def _accept_json(raw: str | bytes) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SourceParseError(f"ответ не JSON: {exc}") from exc


_ACCEPTORS: dict[str, Callable[[Any], Any]] = {
    "page": _accept_page, "torrent": _accept_torrent, "json": _accept_json}


class Delivery:
    """Транспорт модуля sources. Зависимости передаются вызываемыми,
    а не значениями: httpx-клиент и браузерный пул поднимаются позже
    самого модуля."""

    def __init__(self, *, request, http, browser_html, log) -> None:
        self._request = request
        self._http = http
        self._browser_html = browser_html
        self._log = log

    async def fetch(self, service: str, url: str, *, expect: str,
                    mirrors: Iterable[str] = (), transport: str = "auth",
                    accept: Callable[[Any, str], Any] | None = None,
                    timeout: int = PAGE_TIMEOUT,
                    bus_timeout: int = BUS_TIMEOUT,
                    params: dict | None = None) -> Delivered:
        attempts: list[str] = []
        for attempt_url in hosts.candidates(url, mirrors):
            try:
                raw = await self._deliver(service, attempt_url, transport,
                                          timeout, bus_timeout, params)
                value = _ACCEPTORS[expect](raw)
                if accept is not None:
                    value = accept(value, attempt_url)
            except SourceRefusedError:
                raise  # no other host changes this answer (daily limit)
            except Exception as exc:
                attempts.append(f"{urlparse(attempt_url).netloc}: {exc}")
                self._log.warning("%s: хост не сработал — %s", service,
                                  attempts[-1])
                continue
            if attempt_url != url:
                self._log.info("%s: доставлено с %s (в ссылке — %s)", service,
                               urlparse(attempt_url).netloc,
                               urlparse(url).netloc)
            return Delivered(attempt_url, value)
        raise SourceParseError(
            f"{service}: не сработал ни один хост ({'; '.join(attempts)})")

    async def _deliver(self, service: str, url: str, transport: str,
                       timeout: int, bus_timeout: int,
                       params: dict | None) -> str | bytes:
        if transport == "browser":
            return await self._browser_html(service, url)
        if transport == "auth":
            payload: dict[str, Any] = {"service": service, "url": url,
                                       "timeout": timeout}
            if params is not None:
                payload["params"] = params
            reply = await self._request("trackerauth.fetch", payload,
                                        timeout=bus_timeout)
            if reply["status"] != 200:
                raise SourceParseError(f"HTTP {reply['status']}")
            if "text" in reply:
                return reply["text"]
            return base64.b64decode(reply["content_b64"])
        resp = await self._http().get(url)
        resp.raise_for_status()
        if "text/html" in resp.headers.get("content-type", ""):
            return resp.text
        return resp.content
