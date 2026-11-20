"""The only way out of `sources`: delivery with a walk over the hosts.

Every call to a source goes through `Delivery.fetch`, so a path that forgot to move to a live host, or to check WHAT arrived,
is not made in the module (finding 68). Earlier the invariant "be able to move" held only for the parsing of pages, while the
download of a .torrent and the API of anilibria went to the host of the link: a dead host meant a refusal of the pipeline.

Transports: `auth` is through trackerauth (sessions, the anti-bot challenge), `http` is a direct httpx, `browser` is the shared
Playwright pool.

The expected form of the result is declared (`expect`: page | torrent | json) and checked deterministically: a stub of a site
with the code 200 is no answer. An attempt succeeds when the result is delivered AND accepted: the check of the domain
(`accept`) runs INSIDE the attempt, so a page that cannot be parsed leads to a move to the next host, not to a refusal.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from core.contracts import FetchRequest
from core.reasons import describe, reason
from core.topics import TRACKERAUTH_FETCH

from . import hosts, parsers
from .parsers import SourceParseError, SourceRefusedError

TORRENT_TIMEOUT = 30       # seconds for a request of a .torrent to the tracker
PAGE_TIMEOUT = 20          # seconds for a request of a page


@dataclass(frozen=True)
class Delivered:
    """What arrived and from which URL (the host actually used is part of the contract)."""
    url: str
    value: Any


def _accept_page(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        raise SourceParseError(reason("expected_page_got_file"))
    return raw


def _accept_torrent(raw: str | bytes) -> bytes:
    if isinstance(raw, str):
        parsers.raise_not_a_torrent(raw)
    # Metainfo is a bencoded dict with an 'info' key — the only answer
    # that proves a file arrived rather than the site's own page.
    if not (raw.startswith(b"d") and b"4:info" in raw):
        raise SourceParseError(reason("not_torrent_file"))
    return raw


def _accept_json(raw: str | bytes) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SourceParseError(reason("answer_not_json", cause=str(exc))) from exc


_ACCEPTORS: dict[str, Callable[[Any], Any]] = {
    "page": _accept_page, "torrent": _accept_torrent, "json": _accept_json}


class Delivery:
    """The transport of the module sources. The dependencies are passed as callables, not values: the httpx client and the
    browser pool come up later than the module itself. `reply` is `BaseModule.reply_of`: the answer of a neighbour as its model."""

    def __init__(self, *, reply, http, browser_html, log) -> None:
        self._reply = reply
        self._http = http
        self._browser_html = browser_html
        self._log = log

    async def fetch(self, service: str, url: str, *, expect: str,
                    mirrors: Iterable[str] = (), transport: str = "auth",
                    accept: Callable[[Any, str], Any] | None = None,
                    timeout: int = PAGE_TIMEOUT,
                    params: dict | None = None) -> Delivered:
        attempts: list[str] = []
        for attempt_url in hosts.candidates(url, mirrors):
            try:
                raw = await self._deliver(service, attempt_url, transport,
                                          timeout, params)
                value = _ACCEPTORS[expect](raw)
                if accept is not None:
                    value = accept(value, attempt_url)
            except SourceRefusedError:
                raise  # no other host changes this answer (daily limit)
            except Exception as exc:
                attempts.append(reason("host_failed", host=urlparse(attempt_url).netloc, cause=str(exc)))
                self._log.warning("%s: хост не сработал — %s", service, describe(attempts[-1]))
                continue
            if attempt_url != url:
                self._log.info("%s: доставлено с %s (в ссылке — %s)", service,
                               urlparse(attempt_url).netloc,
                               urlparse(url).netloc)
            return Delivered(attempt_url, value)
        raise SourceParseError(reason("no_host_worked", service=service, causes=attempts))

    async def _deliver(self, service: str, url: str, transport: str,
                       timeout: int,
                       params: dict | None) -> str | bytes:
        if transport == "browser":
            return await self._browser_html(service, url)
        if transport == "auth":
            # Only what was set goes: a service that is not VK takes no params.
            asked = FetchRequest(service=service, url=url, timeout=timeout, **({} if params is None else {"params": params}))
            reply = await self._reply(TRACKERAUTH_FETCH, asked.model_dump(mode="json", exclude_unset=True))
            if reply.status != 200:
                raise SourceParseError(f"HTTP {reply.status}")
            if reply.text is not None:
                return reply.text
            return base64.b64decode(reply.content_b64)
        resp = await self._http().get(url)
        resp.raise_for_status()
        if "text/html" in resp.headers.get("content-type", ""):
            return resp.text
        return resp.content
