"""Alloha: one voice-over of one episode, read and downloaded by the native client (alloha_session.py: the client
profile, the signature `Borth` of each open, the socket that keeps access to the CDN, the reports of the player).
An absent or explicitly rejected profile is recovered by one shared Chromium capture, validated on the CDN and cached
encrypted (alloha_profile.py). The failed transfer then starts again with a fresh session and an empty temporary file.

The document of the iframe is an ordinary page of the host (it wants the site as its referer) and lists every voice-over,
season and episode the player has of the title, each with the number its files are asked by. So an ask begins with that
document: a voice-over or an episode the player does not have is known at once, and only an episode it lists is asked
for its files (`POST /bnsi/movies/<id>`, signed afresh from the document). The answer lists the HLS of the episode by
height. The host answers two asks of files a second: every ask of the player goes at that pace.

A download is the player's own (`transfer`): the CDN gives the segments only to a session that plays, with headers the
socket of that session renews, so the stream is never handed to a downloader of its own; the download module names the
file and the player takes the segments into it (`sources.video.fetch`).
"""
from __future__ import annotations

import html
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from core import storage
from core.reasons import reason

from ..policy import Pacer
from .alloha_profile import Profiles, capture_profile
from .alloha_session import (CONNECTIONS, REQUEST_TIMEOUT_S, AllohaRefused, AllohaSession, Profile, chrome_profile,
                             tracks_of)
from .alloha_transfer import ProgressCb, _media, playlist, transfer
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable


# The iframe of the player: any host of Alloha, known by the title token in its address.
_FRAME = re.compile(r"""<iframe\b[^>]*\b(?:data-)?src=["']([^"']*\btoken_movie=[^"']*)["']""", re.IGNORECASE)
_LINK = re.compile(r"^alloha=(.+)$")
# The list of the files of the title in the document of the iframe: a JSON text in a string of its script.
_FILES = re.compile(r"\bfileList\s*=\s*JSON\.parse\('(.*?)'\)\s*;", re.DOTALL)
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_PLAIN_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "0": "\0"}
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


class AllohaPlayer:
    name = "alloha"
    label = "Alloha"
    series_scope = False

    def __init__(self, profile: Profile | None = None, client: Callable[[Profile], Any] | None = None) -> None:
        self._profiles, self._client = Profiles(profile), client or _chrome_client
        self._asks = Pacer(lambda: ASK_INTERVAL_S)

    def configure(self, *, alloha_profile: Profile | None = None, alloha_client: Callable | None = None,
                  alloha_browser_getter: Callable | None = None, alloha_profile_path: str | None = None, **_wiring) -> None:
        if alloha_profile is not None:
            self._profiles.profile = alloha_profile
            self._profiles.generation += 1
        if alloha_client is not None:
            self._client = alloha_client
        if alloha_profile_path is not None:
            self._profiles.path = Path(alloha_profile_path)
        if alloha_browser_getter is not None:
            async def capture(url: str) -> Profile:
                page, iframe = parse_link(url)
                return await capture_profile(alloha_browser_getter, _absolute(iframe), page)
            self._profiles.capture = capture

    async def close(self) -> None:
        await self._profiles.close()

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

    @asynccontextmanager
    async def _session(self, profile: Profile | None = None) -> AsyncIterator[AllohaSession]:
        await self._profiles.load()
        profile = profile or self._profiles.profile
        if profile is None:
            raise AllohaRefused(reason("alloha_no_profile"))
        http = self._client(profile)
        # A running session keeps its identity even when another download refreshes the shared profile.
        session = AllohaSession(profile, http, media=lambda: self._client(profile))
        try:
            yield session
        finally:
            await session.close()
            await http.close()

    async def _opened(self, session: AllohaSession, link: str) -> tuple[str, Reading | None]:
        """The document of the iframe of the link, and what it settles alone: the player has no such video (its host
        answers 404, or the list of its files has no such episode)."""
        parsed = parse_link(link)
        if parsed is None:
            raise AllohaRefused(reason("not_alloha_link"))
        page_url, iframe = parsed
        try:
            page = await session.document(_absolute(iframe), _parent(page_url))
        except AllohaRefused as exc:
            if exc.status == 404:
                return "", Reading(Kind.NOT_FOUND, 404, message=reason("alloha_no_video"))
            raise
        catalog = catalog_of(page)
        reading = _in_catalog(catalog, _named(iframe))[0] if catalog is not None else None
        return page, reading

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        try:
            verify = lambda candidate: self._verify(ref.url, candidate)
            profile, generation = await self._profiles.ensure(ref.url, verify)
            for attempt in range(2):
                try:
                    async with self._session(profile) as session:
                        page, settled = await self._opened(session, ref.url)
                        if settled is not None:
                            return Answer(settled.status, reading=settled)
                        await self._asks.wait()
                        data = await session.answer(page)
                    break
                except AllohaRefused as exc:
                    if attempt or not exc.profile_rejected or self._profiles.capture is None:
                        raise
                    await self._profiles.refresh(generation, ref.url, verify)
                    profile, generation = self._profiles.profile, self._profiles.generation
        except AllohaRefused as exc:
            if exc.status is not None:
                return Answer(exc.status, error=str(exc), reading=Reading(Kind.HTTP_ERROR, exc.status, message=str(exc)))
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        return Answer(200, reading=read_answer(data))

    def read(self, answer: Answer) -> Reading:
        return answer.reading if answer.reading is not None else Reading(Kind.NO_ANSWER, message=answer.error)

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """A download of Alloha is the player's own (`transfer`): the answer names the link and that the player takes it."""
        await self._profiles.load()
        if self._profiles.profile is None and self._profiles.capture is None:
            raise StreamUnavailable(reason("player_failed", player=self.label,
                                           cause=reason("alloha_no_profile")), False)
        return {"stream_url": url, "player_fetch": True}

    async def transfer(self, url: str, target: str, max_height: int | None, connections: int,
                       on_progress: ProgressCb) -> dict:
        """The episode of the link into `target`: the best height within the cap of the voice-over the iframe names, by
        `connections` at once (at most CONNECTIONS). {seconds, longest, height} of its playlist. Raises
        AllohaRefused."""
        with storage.hold(target):
            if await storage.call(os.path.lexists, target):
                raise AllohaRefused(reason("alloha_target_exists"), final=True)
            verify = lambda candidate: self._verify(url, candidate, max_height)
            profile, generation = await self._profiles.ensure(url, verify)
            for attempt in range(2):
                try:
                    async with self._session(profile) as session:
                        listed, track = await self._playing(session, url, max_height)
                        await on_progress({"phase": "link", "source_seconds": listed.seconds or None})
                        await session.report(listed.seconds)
                        await transfer(session, listed, target, max(1, min(connections, CONNECTIONS)), on_progress)
                        return {"seconds": listed.seconds, "longest": listed.longest, "height": track.height}
                except AllohaRefused as exc:
                    if attempt or not exc.profile_rejected or self._profiles.capture is None:
                        raise
                    await on_progress({"phase": "link", "progress": 0})
                    await self._profiles.refresh(generation, url, verify)
                    # This call owned a previously absent target. Do not combine segments from different sessions.
                    if not await storage.discard(target):
                        raise AllohaRefused(reason("alloha_retry_cleanup_failed")) from None
                    profile, generation = self._profiles.profile, self._profiles.generation

    async def _playing(self, session: AllohaSession, url: str, max_height: int | None):
        page, settled = await self._opened(session, url)
        if settled is not None:
            raise AllohaRefused(settled.message or reason("alloha_no_episode"), status=settled.status, final=True)
        await self._asks.wait()
        tracks = tracks_of(await session.answer(page))
        if not tracks:
            raise AllohaRefused(reason("alloha_no_files"), final=True)
        voice = tracks[0].audio_id
        fitting = [each for each in tracks if each.audio_id == voice and (not max_height or each.height <= max_height)]
        if not fitting:
            raise AllohaRefused(reason("alloha_no_height", height=f"{max_height}p"), final=True)
        track = max(fitting, key=lambda each: each.height)
        await session.authorize(page)
        await session.play(track, _connector(session.http))
        return await playlist(session, track.urls[0]), track

    async def _verify(self, url: str, profile: Profile, max_height: int | None = None) -> None:
        async with self._session(profile) as session:
            listed, _track = await self._playing(session, url, max_height)
            await session.report(listed.seconds)
            if listed.init:
                await _media(session, listed.init)
            await _media(session, listed.segments[0][0])


def _in_catalog(catalog: dict[tuple[str, str, str], str], named: dict[str, str]) -> tuple[Reading | None, str | None]:
    """(a reading, None) when the list of the player has no such episode, else (None, the number of its files)."""
    files = catalog.get((named.get("translation", ""), named.get("season", ""), named.get("episode", "")))
    if files is None:
        return Reading(Kind.NOT_FOUND, 200, message="the player lists no such episode of this voice-over"), None
    return None, files


def _chrome_client(profile: Profile):
    """The HTTP client of a session: the TLS of the Chrome of the profile, no headers of its own."""
    from curl_cffi import requests as curl
    return curl.AsyncSession(impersonate=chrome_profile(profile.user_agent), default_headers=False)


def _connector(http) -> Callable[..., Any]:
    """How a session opens the socket its host names: by its own client (the Chrome client with the deflate of the
    browser, or the client of a stand)."""
    async def connect(url: str, headers: dict[str, str]):
        try:
            from curl_cffi.const import CurlOpt, CurlWsOption
        except ImportError:
            return await http.ws_connect(url, headers=headers, timeout=REQUEST_TIMEOUT_S)
        return await http.ws_connect(url, headers=headers, timeout=REQUEST_TIMEOUT_S,
                                     curl_options={CurlOpt.WS_OPTIONS: CurlWsOption.PERMESSAGE_DEFLATE})
    return connect
