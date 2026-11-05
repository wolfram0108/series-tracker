"""Alloha: the iframe of one voice-over of one episode, opened in the browser of the module.

The player asks its host for the files (`POST /bnsi/movies/<id>`) with a header its own script builds from the browser,
so the iframe is loaded in a real browser under the page that embeds it (a blank page at the address of the site is
enough: the iframe checks that it is framed by that site), and the answer is caught. The answer lists the HLS of the
episode by height. The CDN of the files (VK) gives them only with the access headers the player sends once it plays, so a
download starts the player and catches the first request that carries them; those headers serve every height of the
episode outside the browser. One open is one ask: whether to ask again is decided in `policy`.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from core.reasons import reason

from ..policy import probe_from, settle
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .vk_files import pick_file

logger = logging.getLogger("sources")

# The iframe of the player: any host of Alloha, known by the title token in its address.
_FRAME = re.compile(r"""<iframe\b[^>]*\b(?:data-)?src=["']([^"']*\btoken_movie=[^"']*)["']""", re.IGNORECASE)
_LINK = re.compile(r"^alloha=(.+)$")
_ANSWER_PATH = "/bnsi/movies/"
# The headers of a request that a client sets itself; the rest of what the player sent is given to the download.
_OWN_HEADERS = frozenset({"host", "content-length", "connection", "accept-encoding"})
_ACCESS_HEADER = "authorizations"
_BLANK = "<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>"
_INSERT = """(src) => { document.body.innerHTML = ''; const frame = document.createElement('iframe');
    frame.src = src; frame.allow = 'autoplay; encrypted-media; fullscreen';
    frame.style.width = '1280px'; frame.style.height = '900px'; document.body.appendChild(frame); }"""
# Safeguards, not pacing: the player answers in a few seconds; the CDN request follows the play button at once, unless an
# advert plays first (some 15-20 s, as the reference resolver of the owner measured). Past them the ask has no answer.
OPEN_TIMEOUT_S = 30.0
ANSWER_TIMEOUT_S = 30.0
PLAY_TIMEOUT_S = 30.0
# Browser pages at once: each is a whole player, the asks of a table come in a burst.
PARALLEL_PAGES = 2


def link_of(page_url: str, iframe: str) -> str:
    """The candidate link of one episode of a voice-over: the page that embeds it, and the iframe it embeds."""
    return f"{page_url}#alloha={quote(_absolute(iframe), safe='')}"


def parse_link(url: str) -> tuple[str, str] | None:
    """(the page, the iframe) of a candidate link."""
    parts = urlsplit(url)
    found = _LINK.match(parts.fragment)
    if not found:
        return None
    return parts._replace(fragment="").geturl(), unquote(found.group(1))


def _absolute(iframe: str) -> str:
    return "https:" + iframe if iframe.startswith("//") else iframe


def read_answer(data: Any) -> Reading:
    """The files of the voice-over the iframe names: the track the player marks as chosen (the answer may list the other
    voice-overs of the episode too), the first one when none is marked."""
    sources = data.get("hlsSource") if isinstance(data, dict) else None
    if not isinstance(sources, list) or not sources:
        return Reading(Kind.NOT_PLAYER, 200, message="no hlsSource")
    chosen = next((each for each in sources if isinstance(each, dict) and each.get("default") is True), sources[0])
    quality = chosen.get("quality") if isinstance(chosen, dict) else None
    files: dict[int, str] = {}
    for height, address in (quality or {}).items() if isinstance(quality, dict) else ():
        # An address may be given as "<one> or <another>": the first is the player's own choice.
        first = str(address).split(" or ")[0].strip()
        if str(height).isdigit() and first:
            files[int(height)] = first
    if not files:
        return Reading(Kind.NO_FILE, 200)
    return Reading(Kind.PLAYABLE, 200, tuple(sorted(files)), title=str(chosen.get("label") or ""),
                   files=tuple(sorted(files.items())))


class AllohaError(RuntimeError):
    """An open of the iframe that did not come to an answer; it is an unsure answer, never "absent"."""


class AllohaPlayer:
    name = "alloha"
    label = "Alloha"
    series_scope = False

    def __init__(self, browser_getter: Callable | None = None) -> None:
        self._browser_getter = browser_getter
        self._pages = asyncio.Semaphore(PARALLEL_PAGES)

    def configure(self, *, browser_getter=None, **_wiring) -> None:
        if browser_getter is not None:
            self._browser_getter = browser_getter

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        match = _FRAME.search(text)
        return self.ref_of(page_url, html.unescape(match.group(1))) if match else None

    @staticmethod
    def ref_of(page_url: str, iframe: str) -> PlayerRef:
        return PlayerRef("alloha", link_of(page_url, iframe))

    @staticmethod
    def video_of(url: str) -> str:
        """The title, the voice-over, the season and the episode the iframe names: the same on every page."""
        parsed = parse_link(url)
        if parsed is None:
            return ""
        query = {key: values[0] for key, values in parse_qs(urlsplit(parsed[1]).query).items()}
        if not query.get("token_movie"):
            return ""
        return "/".join(query.get(key, "") for key in ("token_movie", "translation", "season", "episode"))

    @staticmethod
    def owns(url: str) -> bool:
        return parse_link(url) is not None

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        try:
            data, _headers = await self._open(ref.url, play=False)
        except AllohaError as exc:
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        return Answer(200, reading=read_answer(data))

    def read(self, answer: Answer) -> Reading:
        return answer.reading if answer.reading is not None else Reading(Kind.NO_ANSWER, message=answer.error)

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """The episode played once in the browser -> the best file within the cap, with the headers the CDN gives it for."""
        playing, ref = _Playing(self), PlayerRef(self.name, url)
        verdict = await settle(playing, ref, http, url, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None or not playing.headers:
            probe = probe_from(verdict, ref, self.label)
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=probe.reason), True)
        return {"stream_url": picked[1], "headers": dict(playing.headers)}

    async def _open(self, link: str, *, play: bool) -> tuple[Any, dict[str, str]]:
        """(the answer of the player, the access headers of its CDN when `play`) of one open of the iframe."""
        parsed = parse_link(link)
        if parsed is None:
            raise AllohaError("not an Alloha link")
        if self._browser_getter is None:
            raise AllohaError("no browser pool")
        page_url, iframe = parsed
        parts = urlsplit(page_url)
        parent = f"{parts.scheme}://{parts.netloc}/"
        browser = await self._browser_getter()
        async with self._pages:
            context = await browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
            try:
                return await self._played(context, parent, iframe, play)
            except asyncio.TimeoutError as exc:
                raise AllohaError("no answer of the player" if not play else "the player did not start") from exc
            except Exception as exc:  # noqa: BLE001 - a browser that failed is an unsure answer, asked again by the policy
                if isinstance(exc, AllohaError):
                    raise
                raise AllohaError(f"browser: {type(exc).__name__}") from exc
            finally:
                await context.close()

    async def _played(self, context, parent: str, iframe: str, play: bool) -> tuple[Any, dict[str, str]]:
        loop = asyncio.get_running_loop()
        answered: asyncio.Future = loop.create_future()
        accessed: asyncio.Future = loop.create_future()
        page = await context.new_page()

        async def on_response(response) -> None:
            if _ANSWER_PATH in response.url and not answered.done():
                try:
                    data = json.loads(await response.text())
                except Exception:  # noqa: BLE001 - an answer that is not the list is not the one awaited
                    return
                if isinstance(data, dict) and "hlsSource" in data and not answered.done():
                    answered.set_result(data)

        async def on_request(request) -> None:
            if not play or accessed.done() or request.method == "OPTIONS":
                return
            try:
                headers = await request.all_headers()
            except Exception:  # noqa: BLE001 - a request gone with its page carries nothing
                return
            if _ACCESS_HEADER in {key.lower() for key in headers} and not accessed.done():
                accessed.set_result({key: value for key, value in headers.items() if key.lower() not in _OWN_HEADERS})

        page.on("response", on_response)
        page.on("request", on_request)
        await page.route(parent, lambda route: route.fulfill(status=200, content_type="text/html", body=_BLANK))
        await page.goto(parent, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT_S * 1000)
        await page.evaluate(_INSERT, _absolute(iframe))
        data = await asyncio.wait_for(answered, ANSWER_TIMEOUT_S)
        if not play:
            return data, {}
        await page.mouse.click(640, 450)
        return data, await asyncio.wait_for(accessed, PLAY_TIMEOUT_S)


class _Playing:
    """The same player asked to play: its answer, and the access headers it caught (what a download is given)."""

    series_scope = False

    def __init__(self, player: AllohaPlayer) -> None:
        self._player, self.name, self.headers = player, player.name, {}

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        try:
            data, self.headers = await self._player._open(ref.url, play=True)
        except AllohaError as exc:
            logger.info("Alloha did not play %s: %s", ref.url, exc)
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        return Answer(200, reading=read_answer(data))

    def read(self, answer: Answer) -> Reading:
        return self._player.read(answer)
