"""The player Alloha, on a browser that gives the events a real one gives (2026-11-06, under yummyani.me).

The iframe is loaded under a blank page at the address of the site; the player answers `POST /bnsi/movies/<id>` with the
HLS of the episode by height; once it plays, its first request to the CDN carries the access headers (after a CORS
preflight that carries none).
"""
from __future__ import annotations

import json

import pytest

from core.retry import Budget
from modules.sources import policy
from modules.sources.players import alloha
from modules.sources.players.alloha import AllohaPlayer, link_of, parse_link, read_answer
from modules.sources.players.contract import Kind, PlayerRef, StreamUnavailable
from sources_support import STEP_LIMIT_S, within

PAGE = "https://old.yummyani.me/catalog/item/lyubov-pod-gipnozom"
IFRAME = ("//alloha.yani.tv/?token_movie=8bf4&translation=158&season=1&episode=5&token=8b55"
          "&hidden=translation,season,episode")


def answer(*tracks: dict) -> dict:
    return {"hlsSource": list(tracks), "tracks": None, "type": "video", "pnr": "wss://alloha.test/ws/"}


def track(label: str, heights=(360, 720, 1080), default: bool | None = None) -> dict:
    found = {"label": label, "audioId": label, "quality": {
        str(height): f"https://cdn{label}.vkvideo.test/{height}.m3u8 or https://backup.test/{height}.m3u8" for height in heights}}
    return {**found, "default": default} if default is not None else found


ACCESS = {"host": "cdn.vkvideo.test", "authorizations": "token-of-the-episode", "accepts-controls": "control",
          "user-agent": "Mozilla/5.0 Firefox", "origin": "https://alloha.yani.tv", "connection": "keep-alive",
          "accept-encoding": "gzip"}


class Request:
    def __init__(self, method: str, url: str, headers: dict) -> None:
        self.method, self.url, self._headers = method, url, headers

    async def all_headers(self) -> dict:
        return dict(self._headers)


class Response:
    def __init__(self, url: str, body: str) -> None:
        self.url, self._body = url, body

    async def text(self) -> str:
        return self._body


class Page:
    def __init__(self, browser: "Browser") -> None:
        self.browser, self.handlers = browser, {}
        self.mouse = self

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def emit(self, event: str, payload) -> None:
        if event in self.handlers:
            await self.handlers[event](payload)

    async def route(self, url: str, handler) -> None:
        self.browser.routed.append(url)

    async def goto(self, url: str, **_options) -> None:
        self.browser.parents.append(url)

    async def evaluate(self, _script: str, src: str) -> None:
        self.browser.iframes.append(src)
        await self.emit("response", Response("https://alloha.yani.tv/ws-player.js", "console.log(1)"))
        if self.browser.player_answer is not None:
            await self.emit("response", Response("https://alloha.yani.tv/bnsi/movies/285818",
                                                 json.dumps(self.browser.player_answer)))

    async def click(self, _x: int, _y: int) -> None:
        self.browser.clicks += 1
        if self.browser.plays:
            await self.emit("request", Request("OPTIONS", "https://cdn.vkvideo.test/0/abc", {"origin": "x"}))
            await self.emit("request", Request("GET", "https://cdn.vkvideo.test/0/abc", ACCESS))


class Context:
    def __init__(self, browser: "Browser") -> None:
        self.browser = browser

    async def new_page(self) -> Page:
        return Page(self.browser)

    async def close(self) -> None:
        self.browser.closed += 1


class Browser:
    def __init__(self, player_answer: dict | None = None, plays: bool = True) -> None:
        self.player_answer, self.plays = player_answer, plays
        self.routed, self.parents, self.iframes = [], [], []
        self.clicks = self.closed = self.opened = 0

    async def new_context(self, **_options) -> Context:
        self.opened += 1
        return Context(self)


def a_player(browser: Browser | None) -> AllohaPlayer:
    async def getter():
        return browser
    return AllohaPlayer(getter if browser is not None else None)


@pytest.fixture
def quick(monkeypatch):
    """A player that never answers is told by the safeguard: short here, so that the test does not wait the real one."""
    monkeypatch.setattr(alloha, "ANSWER_TIMEOUT_S", 0.05)
    monkeypatch.setattr(alloha, "PLAY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(policy, "DEFAULT", Budget(attempts=2, pauses=(0.0,), limit_s=5.0))


# --- the link and the iframe ------------------------------------------------------------------------------------------------------

def test_a_page_embeds_alloha_by_an_iframe_with_the_token_of_the_title_and_the_link_keeps_page_and_iframe():
    player = AllohaPlayer()
    frame = f'<iframe src="{IFRAME.replace("&", "&amp;")}" allowfullscreen></iframe>'
    ref = player.find(frame, PAGE)

    assert parse_link(ref.url) == (PAGE, "https:" + IFRAME)
    assert player.find('<iframe src="https://alloha.test/embed/1"></iframe>', PAGE) is None
    assert AllohaPlayer.owns(ref.url) and not AllohaPlayer.owns(PAGE) and not AllohaPlayer.owns(PAGE + "#kodik=1/2/3")
    # The video is the title, the voice-over, the season and the episode: the same on any page that embeds it.
    assert AllohaPlayer.video_of(ref.url) == "8bf4/158/1/5" == AllohaPlayer.video_of(link_of("https://other.test/a", IFRAME))


def test_the_answer_of_the_player_is_read_into_the_files_of_the_voice_it_marks():
    marked = read_answer(answer(track("1", (360, 720)), track("2", (480, 1080), default=True)))
    first = read_answer(answer(track("1", (360, 720)), track("2", (480, 1080))))

    assert (marked.kind, marked.resolutions, marked.title) == (Kind.PLAYABLE, (480, 1080), "2")
    # Of "<one> or <another>" the first address is the player's own choice.
    assert dict(marked.files)[1080] == "https://cdn2.vkvideo.test/1080.m3u8" and first.resolutions == (360, 720)
    assert read_answer({"hlsSource": []}).kind is Kind.NOT_PLAYER and read_answer([]).kind is Kind.NOT_PLAYER
    assert read_answer(answer({"label": "1", "quality": {}})).kind is Kind.NO_FILE


# --- an ask: the iframe opened under the site, the answer caught -------------------------------------------------------------------

async def test_an_ask_opens_the_iframe_under_a_blank_page_of_the_site_and_reads_the_answer_without_playing():
    browser = Browser(answer(track("1")))
    player = a_player(browser)
    reading = player.read(await within(player.fetch(player.ref_of(PAGE, IFRAME), None, PAGE), STEP_LIMIT_S, "the ask"))

    assert (reading.kind, reading.resolutions) == (Kind.PLAYABLE, (360, 720, 1080))
    assert browser.parents == browser.routed == ["https://old.yummyani.me/"] and browser.iframes == ["https:" + IFRAME]
    assert browser.clicks == 0 and browser.closed == browser.opened == 1


async def test_an_ask_with_no_answer_or_no_browser_is_unsure_and_the_page_is_closed(quick):
    browser = Browser(None)
    silent = a_player(browser)
    unanswered = silent.read(await within(silent.fetch(silent.ref_of(PAGE, IFRAME), None, PAGE), STEP_LIMIT_S, "the ask"))
    no_pool = a_player(None)
    unequipped = no_pool.read(await no_pool.fetch(no_pool.ref_of(PAGE, IFRAME), None, PAGE))

    assert unanswered.kind is Kind.NO_ANSWER and browser.closed == browser.opened == 1
    assert unequipped.kind is Kind.NO_ANSWER and "browser" in unequipped.message


# --- a stream: the player played once, the access headers caught -------------------------------------------------------------------

async def test_a_stream_is_the_best_file_within_the_cap_with_the_headers_of_the_request_that_carried_the_access():
    browser = Browser(answer(track("1")))
    player = a_player(browser)
    got = await within(player.stream(player.ref_of(PAGE, IFRAME).url, None, 720, None), STEP_LIMIT_S, "the stream")

    assert got["stream_url"] == "https://cdn1.vkvideo.test/720.m3u8"
    # The preflight carries no access; what a client sets itself is not handed on.
    assert got["headers"] == {"authorizations": "token-of-the-episode", "accepts-controls": "control",
                              "user-agent": "Mozilla/5.0 Firefox", "origin": "https://alloha.yani.tv"}
    assert browser.clicks == 1 and browser.closed == browser.opened == 1


async def test_a_player_that_does_not_start_is_asked_again_by_the_policy_and_then_is_no_stream(quick):
    browser = Browser(answer(track("1")), plays=False)
    player = a_player(browser)

    with pytest.raises(StreamUnavailable) as refused:
        await within(player.stream(player.ref_of(PAGE, IFRAME).url, None, None, None), STEP_LIMIT_S, "the stream")

    assert refused.value.transient and browser.opened == browser.closed == 2 and browser.clicks == 2


def test_the_link_names_the_player_and_another_link_is_not_its_own():
    assert PlayerRef("alloha", link_of(PAGE, IFRAME)).url.startswith(PAGE + "#alloha=")
    assert parse_link(PAGE) is None and AllohaPlayer.video_of(PAGE) == ""
