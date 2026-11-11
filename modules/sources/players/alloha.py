"""Alloha: the iframe of one voice-over of one episode, opened in the browser of the module.

The player asks its host for the files (`POST /bnsi/movies/<id>`) with a header its own script builds from the browser,
so the iframe is loaded in a real browser under the page that embeds it (a blank page at the address of the site is
enough: the iframe checks that it is framed by that site), and the answer is caught. The answer lists the HLS of the
episode by height. The CDN of the files (VK) gives them only with the access headers the player sends once it plays, so a
download starts the player and catches the first request that carries them; those headers serve every height of the
episode outside the browser. One open is one ask: whether to ask again is decided in `policy`.

The document of the iframe is an ordinary page of the host (it wants the site as its referer) and lists every voice-over,
season and episode the player has of the title, each with the number its files are asked by. So an ask begins with that
document, without a browser: a voice-over or an episode the player does not have is known at once (a quarter of a second),
not by waiting for a player that never asks for files.

An open costs seconds (a page of the browser, the scripts of the player); the player itself switches to another episode
of the season it shows in a quarter of one, answering the same request for it — one switch at a time (its request is
signed for a single use, asks sent together are refused). So the videos of a table are read one open for each season of
each voice-over (`read_batch`), each further episode a switch inside it, every answer told from the others by the number
of its files; an episode the switch did not settle is asked on its own. A page opened to read loads the player alone:
the stream, the adverts and the pictures are not fetched.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Sequence
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from core.reasons import reason

from ..policy import Pacer, probe_from, settle
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .http import get_answer
from .vk_files import pick_file

logger = logging.getLogger("sources")

# The iframe of the player: any host of Alloha, known by the title token in its address.
_FRAME = re.compile(r"""<iframe\b[^>]*\b(?:data-)?src=["']([^"']*\btoken_movie=[^"']*)["']""", re.IGNORECASE)
_LINK = re.compile(r"^alloha=(.+)$")
_ANSWER_PATH = "/bnsi/movies/"
# The list of the files of the title in the document of the iframe: a JSON text in a string of its script.
_FILES = re.compile(r"\bfileList\s*=\s*JSON\.parse\('(.*?)'\)\s*;", re.DOTALL)
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_PLAIN_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "0": "\0"}
# What a page opened to read does not fetch: the player's own host serves all the read needs.
_UNREAD_KINDS = frozenset({"image", "media", "font"})
# The headers of a request that a client sets itself; the rest of what the player sent is given to the download.
_OWN_HEADERS = frozenset({"host", "content-length", "connection", "accept-encoding"})
_ACCESS_HEADER = "authorizations"
_REFUSED = "refused"
_BLANK = "<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>"
_INSERT = """(src) => { document.body.innerHTML = ''; const frame = document.createElement('iframe');
    frame.src = src; frame.allow = 'autoplay; encrypted-media; fullscreen';
    frame.style.width = '1280px'; frame.style.height = '900px'; document.body.appendChild(frame); }"""
# The list of episodes of the player (hidden when the site names the episode): a click on an item asks for that episode.
_SWITCH = """(episode) => { const item = [...document.querySelectorAll('[data-select^="episode"] .select__drop-item')]
    .find(each => each.dataset.id === episode); if (!item) return false; item.click(); return true; }"""
# Safeguards, not pacing: the player answers in a few seconds; the CDN request follows the play button at once, unless an
# advert plays first (some 15-20 s, as the reference resolver of the owner measured). Past them the ask has no answer.
OPEN_TIMEOUT_S = 30.0
ANSWER_TIMEOUT_S = 30.0
PLAY_TIMEOUT_S = 30.0
# A switch answers in a quarter of a second (measured 06.11); past this the open is left and the rest asked one by one.
SWITCH_TIMEOUT_S = 10.0
# The manifest of the stream is a few lines: past this the host does not answer here.
MANIFEST_TIMEOUT_S = 20.0
# Browser pages at once: each is a whole player, the asks of a table come in a burst.
PARALLEL_PAGES = 2
# The host answers two asks of files a second, whatever page they come from (measured 11.11: 49 episodes at one every
# 0.5 s were all answered; at one every 0.35 s it refused some with the 404 of a video it has not). The asks of every
# page of the player go at that pace, so a refusal by rate is the exception, not the way the read finds its limit.
ASK_INTERVAL_S = 0.5


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


def _named(iframe: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(iframe).query).items()}


def _parent(page_url: str) -> str:
    parts = urlsplit(page_url)
    return f"{parts.scheme}://{parts.netloc}/"


def _unescaped(text: str) -> str:
    """A string of a script as the text it stands for."""
    def one(found: re.Match) -> str:
        escape = found.group(1)
        return chr(int(escape[1:], 16)) if escape[0] in "ux" and len(escape) > 1 else _PLAIN_ESCAPES.get(escape, escape)
    return _ESCAPE.sub(one, text)


def catalog_of(document: str) -> dict[tuple[str, str, str], str] | None:
    """The files the player has of the title, as the document of its iframe lists them: (voice-over, season, episode) ->
    the number the files of that episode are asked by. None when the document lists no episodes (not a series, another
    form): nothing is concluded from it then."""
    found = _FILES.search(document)
    if not found:
        return None
    try:
        listed = json.loads(_unescaped(found.group(1)))
        voices = listed["all"]
        catalog: dict[tuple[str, str, str], str] = {}
        for voice in voices.values():
            for season, episodes in voice["file"].items():
                for episode, file in episodes.items():
                    catalog[(str(file["id_translation"]), str(season), str(episode))] = str(file["id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return catalog or None


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


class _Shown:
    """One iframe open in a page of the browser: the answers of the player by the number of the files each is about, the
    access headers of its CDN once it plays, and the switch to another episode."""

    def __init__(self, page, play: bool) -> None:
        self.page, self._play = page, play
        self._answers: dict[str, Any] = {}  # the number of the files -> its answer not taken yet, in the order they came
        self._came = asyncio.Event()
        self.accessed: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_response(self, response) -> None:
        if _ANSWER_PATH not in response.url:
            return
        try:
            data = json.loads(await response.text())
        except Exception:  # noqa: BLE001 - an answer that is not the list is not the one awaited
            return
        if isinstance(data, dict) and ("hlsSource" in data or response.status >= 400):
            # A refusal of the files (the host answers 404 past its rate) is an answer too: the ask is not waited out.
            refused = {_REFUSED: response.status} if "hlsSource" not in data else data
            self._answers[urlsplit(response.url).path.rsplit("/", 1)[-1]] = refused
            self._came.set()

    async def on_finished(self, request) -> None:
        """The access headers of the first request to the CDN that the CDN answered: a request that went out and got no
        answer (a host not resolved yet, a refusal) proves no address and no key."""
        if not self._play or self.accessed.done() or request.method == "OPTIONS":
            return
        try:
            headers = await request.all_headers()
            if _ACCESS_HEADER not in {key.lower() for key in headers}:
                return
            response = await request.response()
        except Exception:  # noqa: BLE001 - a request gone with its page carries nothing
            return
        if response is not None and response.status < 400 and not self.accessed.done():
            self.accessed.set_result({key: value for key, value in headers.items() if key.lower() not in _OWN_HEADERS})

    def _next(self, files: str | None) -> str | None:
        if files is not None:
            return files if files in self._answers else None
        return next(iter(self._answers), None)

    async def answer(self, timeout_s: float, files: str | None = None) -> Any:
        """The answer about the files of this number; with no number known, the next answer that came."""
        async def wait() -> Any:
            while (found := self._next(files)) is None:
                self._came.clear()
                await self._came.wait()
            return self._answers.pop(found)
        data = await asyncio.wait_for(wait(), timeout_s)
        if _REFUSED in data:
            raise AllohaError(f"the host refused the files of the video ({data[_REFUSED]})")
        return data

    async def switch(self, episode: str) -> bool:
        """Ask the player for another episode of the season it shows; False when its list has no such episode."""
        frames = self.page.main_frame.child_frames
        return bool(frames) and bool(await frames[0].evaluate(_SWITCH, episode))

    async def play(self) -> dict[str, str]:
        await self.page.mouse.click(640, 450)
        return await asyncio.wait_for(self.accessed, PLAY_TIMEOUT_S)


@dataclass(frozen=True)
class _Listed:
    """What the document of the iframe says of a link: a reading when it settles the video (the player has none such),
    else the number of its files when the document lists it (None: the document says nothing, the player is asked)."""
    reading: Reading | None = None
    files: str | None = None
    catalog: dict[tuple[str, str, str], str] | None = None


class AllohaPlayer:
    name = "alloha"
    label = "Alloha"
    series_scope = False

    def __init__(self, browser_getter: Callable | None = None) -> None:
        self._browser_getter = browser_getter
        self._pages = asyncio.Semaphore(PARALLEL_PAGES)
        self._asks = Pacer(lambda: ASK_INTERVAL_S)

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
        named = _named(parsed[1])
        if not named.get("token_movie"):
            return ""
        return "/".join(named.get(key, "") for key in ("token_movie", "translation", "season", "episode"))

    @staticmethod
    def owns(url: str) -> bool:
        return parse_link(url) is not None

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        listed = await self._listed(http, ref.url)
        if listed.reading is not None:
            return Answer(listed.reading.status, reading=listed.reading)
        try:
            data, _headers = await self._open(ref.url, play=False, files=listed.files)
        except AllohaError as exc:
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        return Answer(200, reading=read_answer(data))

    def read(self, answer: Answer) -> Reading:
        return answer.reading if answer.reading is not None else Reading(Kind.NO_ANSWER, message=answer.error)

    async def _listed(self, http: httpx.AsyncClient, link: str) -> _Listed:
        """What the document of the iframe of the link says, asked as the site that embeds it asks: the player has no
        such video (its host answers 404, or the list of its files has no such episode) — or the number of its files."""
        parsed = parse_link(link)
        if parsed is None or http is None:
            return _Listed()
        page_url, iframe = parsed
        answer = await get_answer(http, _absolute(iframe), _parent(page_url))
        if answer.status == 404:
            return _Listed(Reading(Kind.NOT_FOUND, 404, message="the player has no such video"))
        catalog = catalog_of(answer.text) if answer.status == 200 else None
        if catalog is None:
            return _Listed()
        return _Listed(*_in_catalog(catalog, _named(iframe)), catalog)

    async def read_batch(self, refs: Sequence[PlayerRef], http: httpx.AsyncClient, page_url: str) -> dict[PlayerRef, Reading]:
        """What one document and one open of each season of each voice-over settle of these videos; the rest is left to
        `fetch`."""
        seasons: dict[tuple[str, ...], list[tuple[PlayerRef, dict[str, str]]]] = {}
        for ref in refs:
            parsed = parse_link(ref.url)
            named = _named(parsed[1]) if parsed else {}
            if parsed and named.get("token_movie") and named.get("episode"):
                key = (_parent(parsed[0]), named["token_movie"], named.get("translation", ""), named.get("season", ""))
                seasons.setdefault(key, []).append((ref, named))
        settled: dict[PlayerRef, Reading] = {}
        for found in await asyncio.gather(*(self._season(http, members) for members in seasons.values())):
            settled.update(found)
        return settled

    async def _season(self, http: httpx.AsyncClient, members: list[tuple[PlayerRef, dict[str, str]]]) -> dict[PlayerRef, Reading]:
        """The episodes of one season of one voice-over. The document of the first says what the player has of the title:
        an episode it does not list is settled by that. The listed ones are read in one open: the first by its iframe,
        every other a switch of the player, each answer taken by the number of its files."""
        settled: dict[PlayerRef, Reading] = {}
        listed = await self._listed(http, members[0][0].url)
        if listed.catalog is None:
            # The document settles its own video alone (the player has none such) or nothing: the rest is asked one by
            # one, each by its own document.
            if listed.reading is not None:
                settled[members[0][0]] = listed.reading
            return settled
        asked: list[tuple[PlayerRef, str, str]] = []
        for ref, named in members:
            reading, files = _in_catalog(listed.catalog, named)
            if reading is not None:
                settled[ref] = reading
            else:
                asked.append((ref, named["episode"], files))
        if not asked:
            return settled
        try:
            async with self._shown(asked[0][0].url, play=False) as shown:
                settled[asked[0][0]] = read_answer(await shown.answer(ANSWER_TIMEOUT_S, asked[0][2]))
                for ref, episode, files in asked[1:]:
                    await self._asks.wait()
                    if await shown.switch(episode):
                        settled[ref] = read_answer(await shown.answer(SWITCH_TIMEOUT_S, files))
        except Exception as exc:  # noqa: BLE001 - what one open did not settle is asked one by one, never lost
            logger.info("Alloha settled %d of %d episodes in one open, the rest is asked one by one: %s",
                        len(settled), len(members), str(exc) or type(exc).__name__)
        return settled

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """The episode played once in the browser -> the best file within the cap, with the headers the CDN gives it for."""
        playing, ref = _Playing(self), PlayerRef(self.name, url)
        verdict = await settle(playing, ref, http, url, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None or not playing.headers:
            probe = probe_from(verdict, ref, self.label)
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=probe.reason), True)
        # The address is the player's, opened in the browser; the download opens it from here. Its host may not resolve
        # here yet (the CDN makes its names as players ask), and then nothing of it would be fetched: that is said now,
        # as a refusal that may pass, not found by the downloader as a stream that never opens.
        opened = await get_answer(http, picked[1], "", headers=dict(playing.headers), timeout=MANIFEST_TIMEOUT_S)
        if opened.status is None or opened.status >= 400:
            cause = reason("stream_unreachable", host=urlsplit(picked[1]).hostname or "",
                           error=opened.error or str(opened.status))
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=cause), True)
        return {"stream_url": picked[1], "headers": dict(playing.headers)}

    async def _open(self, link: str, *, play: bool, files: str | None = None) -> tuple[Any, dict[str, str]]:
        """(the answer of the player, the access headers of its CDN when `play`) of one open of the iframe; `files` is
        the number the document gave the files of the video, when it gave one."""
        try:
            async with self._shown(link, play=play) as shown:
                data = await shown.answer(ANSWER_TIMEOUT_S, files)
                return data, (await shown.play() if play else {})
        except asyncio.TimeoutError as exc:
            raise AllohaError("no answer of the player" if not play else "the player did not start") from exc
        except AllohaError:
            raise
        except Exception as exc:  # noqa: BLE001 - a browser that failed is an unsure answer, asked again by the policy
            raise AllohaError(f"browser: {type(exc).__name__}") from exc

    @asynccontextmanager
    async def _shown(self, link: str, *, play: bool) -> AsyncIterator[_Shown]:
        """The iframe of the link open under a blank page at the address of its site, for as long as the block runs."""
        parsed = parse_link(link)
        if parsed is None:
            raise AllohaError("not an Alloha link")
        if self._browser_getter is None:
            raise AllohaError("no browser pool")
        page_url, iframe = parsed
        parent = _parent(page_url)
        browser = await self._browser_getter()
        async with self._pages:
            context = await browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
            try:
                page = await context.new_page()
                shown = _Shown(page, play)
                page.on("response", shown.on_response)
                page.on("requestfinished", shown.on_finished)
                if not play:
                    await page.route("**/*", _player_alone(urlsplit(_absolute(iframe)).netloc))
                await page.route(parent, lambda route: route.fulfill(status=200, content_type="text/html", body=_BLANK))
                await page.goto(parent, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT_S * 1000)
                await self._asks.wait()  # the iframe asks for the files of its episode as it loads
                await page.evaluate(_INSERT, _absolute(iframe))
            except Exception as exc:  # noqa: BLE001 - a browser that failed is an unsure answer, asked again by the policy
                await context.close()
                raise AllohaError(f"browser: {type(exc).__name__}") from exc
            except BaseException:  # a cancelled ask leaves no page behind it in the browser
                await asyncio.shield(context.close())
                raise
            try:
                yield shown
            finally:
                await asyncio.shield(context.close())


def _in_catalog(catalog: dict[tuple[str, str, str], str], named: dict[str, str]) -> tuple[Reading | None, str | None]:
    """(a reading, None) when the list of the player has no such episode, else (None, the number of its files)."""
    files = catalog.get((named.get("translation", ""), named.get("season", ""), named.get("episode", "")))
    if files is None:
        return Reading(Kind.NOT_FOUND, 200, message="the player lists no such episode of this voice-over"), None
    return None, files


def _player_alone(host: str) -> Callable:
    """The route of a page opened to read: the host of the player serves its document, scripts and answers; the stream,
    the adverts and the pictures are not fetched (the read needs the answer of the player and nothing else)."""
    async def route(found) -> None:
        request = found.request
        if request.resource_type in _UNREAD_KINDS or urlsplit(request.url).netloc != host:
            await found.abort()
        else:
            await found.continue_()
    return route


class _Playing:
    """The same player asked to play: its answer, and the access headers it caught (what a download is given)."""

    series_scope = False

    def __init__(self, player: AllohaPlayer) -> None:
        self._player, self.name, self.headers = player, player.name, {}

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        listed = await self._player._listed(http, ref.url)
        if listed.reading is not None:
            return Answer(listed.reading.status, reading=listed.reading)
        try:
            data, self.headers = await self._player._open(ref.url, play=True, files=listed.files)
        except AllohaError as exc:
            logger.info("Alloha did not play %s: %s", ref.url, exc)
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        return Answer(200, reading=read_answer(data))

    def read(self, answer: Answer) -> Reading:
        return self._player.read(answer)
