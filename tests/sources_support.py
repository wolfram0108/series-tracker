"""Support for the tests of the sites and the players: an in-process web (routes over `httpx.MockTransport`), the synthetic
answers of the sites and the players by name (`site_samples`). The database of a test is `schema_db.migrated`: a real
file, since every operation of `Database` opens its own connection."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from site_samples import SAMPLES

ROOT = Path(__file__).resolve().parent.parent


# How long a test waits for what the code under test should end by itself. A read of a whole release is some seconds of
# work; what waits longer waits for an answer that no fake will give, and that is a failure of the test, not a hang.
READ_LIMIT_S = 60.0
STEP_LIMIT_S = 10.0


async def within(awaited, seconds: float, what: str):
    """Await with a limit: the code that waits for ever is a red test with a name, not a suite that hangs until the guard."""
    try:
        return await asyncio.wait_for(awaited, seconds)
    except asyncio.TimeoutError:
        pytest.fail(f"{what} did not end in {seconds:g} s: it waits for something no fake of the test gives")


def text_of(name: str) -> str:
    """A synthetic answer by its name; a name that is not there is a failure that names it."""
    if name not in SAMPLES:
        pytest.fail(f"нет образца {name}")
    return SAMPLES[name]()


def json_of(name: str):
    return json.loads(text_of(name))


Answer = str | tuple[int, str] | httpx.Response


class Web:
    """Routes are tried in the order they were added; the first whose marker is in the address (and whose method fits)
    answers. A route may be a function of the request. What is not routed is `404`."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, str, Answer | Callable[[httpx.Request], Answer]]] = []
        self.asked: list[tuple[str, str, str]] = []
        self.requests: list[httpx.Request] = []

    def route(self, marker: str, answer: "Answer | Callable[[httpx.Request], Answer]" = "", *,
              method: str = "GET", first: bool = False) -> "Web":
        """`first` puts the route before the others: a special case over the general ones already added."""
        self._routes.insert(0 if first else len(self._routes), (method, marker, answer))
        return self

    def count(self, marker: str, method: str = "GET") -> int:
        return sum(1 for asked_method, url, _body in self.asked if asked_method == method and marker in url)

    def forms(self, marker: str) -> list[dict]:
        """The forms posted to the addresses with the marker."""
        from urllib.parse import parse_qsl
        return [dict(parse_qsl(body)) for method, url, body in self.asked if method == "POST" and marker in url]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(request)
        self.asked.append((request.method, url, request.content.decode("utf-8", "replace")))
        for method, marker, answer in self._routes:
            if method == request.method and marker in url:
                given = answer(request) if callable(answer) else answer
                if isinstance(given, httpx.Response):
                    return given
                status, text = given if isinstance(given, tuple) else (200, given)
                return httpx.Response(status, text=text)
        return httpx.Response(404, text="")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))


# --- a link each player owns, and a host that cannot give it ----------------------------------------------------------------------

PAGE = "https://1.ruchime.org/release/test/episode-1"
DUNHUA = "https://dunhuatv.ru/28-x-smotret-onlajn.html"
OWNED = {"vk": "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc", "allvideo": "https://fsst.online/embed/77/",
         "vibix": f"{PAGE}#vibix=1/1", "cvh": f"{DUNHUA}#cvh=missing", "kodik": f"{DUNHUA}#kodik=50228/7bbb4c6b/101",
         "animakima": "https://animakima.ru/pages/x/#animakima=1/2"}
# Whether asking again may help, by what the host did: a video it says is gone is gone, a host that is busy or silent may
# recover, a page that is no player is not confirmed (the manifests of Vibix that do not decode are a final refusal).
TRANSIENT = {mode: {name: expected for name in OWNED}
             for mode, expected in (("gone", False), ("busy", True), ("silent", True), ("garbage", True))}
TRANSIENT["garbage"]["vibix"] = False


def host_that(mode: str) -> Web:
    """A web where everything answers the way a host does that cannot give the video: gone, busy, silent or not a player."""
    def silent(request):
        raise httpx.ConnectError("no route")

    answer = {"gone": (404, ""), "busy": (503, "busy"), "silent": silent,
              "garbage": (200, "<html><body>not a player</body></html>")}[mode]
    return Web().route("", answer).route("", answer, method="POST")
