"""Vibix: one capture of the series catalog through a browser, then an episode is read from its manifests.

It is a series-scope player: the playlist is captured once for the whole series and kept until its signatures
near their end. An episode is asked once, from the best stream down, and the first manifest that reads is its
answer; asking again is decided by `policy`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urldefrag, urljoin

import httpx

from core import storage
from core.contracts import RowVoice
from core.reasons import reason

from .catalog import CatalogError, Entry
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .vibix_protocol import (Episode, KeyStore, Stream, VibixProtocolError, VibixUnavailable, capture_with_browser,
                             decode_playlist, parse_manifest, stream_expires)

logger = logging.getLogger("sources")

REFRESH_MARGIN_S = 600
MANIFEST_TIMEOUT_S = 15.0
# The site switches to Vibix by the whole embed (`vibix-embed`) or by one entry (`vibix:<id>:<type>:<season>:<episodes>`).
_BUTTON = re.compile(r"""switchSource\(\s*["']vibix(?:-embed["']|:)""", re.IGNORECASE)
_LINK = re.compile(r"^vibix=(\d+)/(\d+)$")
_ABSOLUTE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def within_cap(streams, max_height: int | None) -> list:
    """The streams in the order they are tried: the best not above the cap first, then the lower ones, then, when
    nothing fits, the lowest above it. Without a cap, the best first."""
    best_first = sorted(streams, key=lambda item: item.quality, reverse=True)
    if not max_height:
        return best_first
    return [item for item in best_first if item.quality <= max_height] + [
        item for item in reversed(best_first) if item.quality > max_height]


class VibixFailure(RuntimeError):
    """What a download of an episode could not do, as a coded reason; `transient` marks causes that may pass."""

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.reason = message
        self.transient = transient


def link_of(page_url: str, season: int, position: int) -> str:
    """Candidate link of one catalog entry: the entry the table lists is the one that gets downloaded."""
    return f"{page_url}#vibix={season}/{position}"


def parse_link(url: str) -> tuple[int, int] | None:
    """(season position, entry position) of a candidate link; None for any other address."""
    found = _LINK.match(urldefrag(url).fragment)
    return (int(found.group(1)), int(found.group(2))) if found else None


def entry_of(playlist: list[Episode], season: int, position: int) -> Episode | None:
    return next((item for item in playlist if item.season_index == season and item.position == position), None)


def _failed(label: str, reading: Reading) -> VibixFailure:
    """A manifest that did not read, as the reason a download shows."""
    if reading.kind is Kind.NO_ANSWER:
        return VibixFailure(reason("manifest_failed", label=label, error=reading.message), transient=True)
    if reading.kind is Kind.HTTP_ERROR:
        return VibixFailure(reason("manifest_status", label=label, status=reading.status),
                            transient=reading.status in (408, 425, 429) or reading.status >= 500)
    return VibixFailure(reason("manifest_undecoded", label=label, error=reading.message))


def _absolute_url(base_url: str, reference: str) -> str:
    if _ABSOLUTE_URL.match(reference):
        return reference
    # A colon in the first path segment would be parsed as a URL scheme.
    if ":" in reference.split("/", 1)[0] and not reference.startswith("./"):
        reference = "./" + reference
    return urljoin(base_url, reference)


class VibixPlayer:
    name = "vibix"
    label = "Vibix"
    series_scope = True
    # The catalog is one capture of a whole browser session: a failed one is asked once more.
    asks_catalog_again = True

    def __init__(self, browser_getter: Callable | None = None, key_path: str | Path | None = None,
                 manifest_dir: str | Path | None = None) -> None:
        self._browser_getter = browser_getter
        self._key_store = KeyStore(Path(key_path) if key_path else None)
        self._manifest_dir = Path(manifest_dir) if manifest_dir else Path(tempfile.gettempdir())
        self._cache: dict[str, tuple[int, list[Episode], bytes]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # The key file is changed by one capture at a time: a storage hold refuses a second claim on the path.
        self._key_lock = asyncio.Lock()

    def configure(self, *, browser_getter=None, key_path: str | Path | None = None,
                  manifest_dir: str | Path | None = None) -> None:
        """Late wiring from the sources module (browser pool and cache paths)."""
        if browser_getter is not None:
            self._browser_getter = browser_getter
        if key_path is not None:
            self._key_store = KeyStore(Path(key_path))
        if manifest_dir is not None:
            self._manifest_dir = Path(manifest_dir)

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        """The button that switches the page to Vibix, as the other players of the site are found by theirs: the word is
        also in the script every page of the site carries, so a page without the player would show it too, and the catalog
        would be captured through a page that has no button to press."""
        if _BUTTON.search(text) is None:
            return None
        return PlayerRef(name=self.name, url=page_url)

    async def playlist(self, page_url: str) -> tuple[list[Episode], bytes]:
        """The series playlist: captured once per signature window, kept while it is good."""
        page_url = urldefrag(page_url).url
        found = re.search(r"/release/([^/?#]+)", page_url)
        slug = found.group(1) if found else page_url
        lock = self._locks.setdefault(slug, asyncio.Lock())
        async with lock:
            now = int(time.time())
            cached = self._cache.get(slug)
            if cached is not None and cached[0] - now > REFRESH_MARGIN_S:
                return cached[1], cached[2]
            if self._browser_getter is None:
                raise VibixUnavailable(reason("vibix_no_browser_pool"))
            browser = await self._browser_getter()
            capture = await capture_with_browser(browser, page_url)
            key = await self._key_of(capture)
            episodes = decode_playlist(capture.body, key)
            if not episodes:
                raise VibixProtocolError("Vibix API returned an empty playlist")
            expiries = [stream_expires(stream.url) for episode in episodes for stream in episode.streams]
            expiries = [value for value in expiries if value is not None]
            expires = min(expiries) if expiries else now + 3600
            self._cache[slug] = (expires, episodes, key)
            return episodes, key

    @staticmethod
    def catalog_id(source: PlayerRef) -> str:
        """Every page of a series shows the same catalog: the series names it."""
        found = re.search(r"/release/([^/?#]+)", source.url)
        return found.group(1) if found else source.url

    async def entries(self, source: PlayerRef, http: httpx.AsyncClient, page_url: str) -> list[Entry]:
        """The entries of the catalog, season by season; the link of an entry names the page it is asked through."""
        try:
            playlist, _key = await self.playlist(source.url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            raise CatalogError(reason("player_failed", player=self.label, cause=str(exc)),
                               transient=isinstance(exc, VibixUnavailable)) from exc
        return [Entry(key=f"{item.season_index}/{item.position}",
                      ref=PlayerRef(self.name, link_of(page_url, item.season_index, item.position)),
                      text=item.title, group=item.season, season=item.season, voice=RowVoice(name=item.voice),
                      site_fields={"season_index": item.season_index, "position": item.position})
                for item in playlist]

    async def _key_of(self, capture) -> bytes:
        """The decoder key, from the shared key file or recovered from the capture; the file is read and written as a
        storage call, so a stalled disk ends the capture instead of holding it."""
        async with self._key_lock:
            try:
                key, _refreshed = await storage.call(
                    self._key_store.resolve, api_body=capture.body, manifest_body=capture.manifest_body,
                    owns=str(self._key_store.path))
            except OSError as exc:
                raise VibixUnavailable(reason("storage_failed", cause=str(exc))) from exc
        return key

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        target = parse_link(ref.url)
        if target is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a Vibix link"))
        season, position = target
        try:
            playlist, key = await self.playlist(page_url)
        except VibixUnavailable as exc:
            return Answer(None, error=str(exc), reading=Reading(Kind.NO_ANSWER, message=str(exc)))
        except VibixProtocolError as exc:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message=str(exc)))
        episode = entry_of(playlist, season, position)
        if episode is None or not episode.streams:
            return Answer(200, reading=Reading(Kind.NOT_FOUND, 200, message=f"entry {season}/{position}"))
        last = Reading(Kind.NO_FILE, message="no streams")
        for stream in sorted(episode.streams, key=lambda item: item.quality, reverse=True):
            reading, _text = await self._manifest(stream, key, http)
            if reading.kind is Kind.PLAYABLE:
                return Answer(200, reading=reading)
            last = reading
        return Answer(last.status or None, error=last.message, reading=last)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        return Reading(Kind.NO_ANSWER, message=answer.error)

    async def _manifest(self, stream: Stream, key: bytes, http: httpx.AsyncClient) -> tuple[Reading, str | None]:
        """One stream manifest, fetched and decoded; the CDN may announce streams it cannot serve."""
        try:
            response = await http.get(stream.url, follow_redirects=True, timeout=MANIFEST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return Reading(Kind.NO_ANSWER, message=type(exc).__name__), None
        if response.status_code != 200:
            logger.warning("Vibix manifest HTTP %s for %s", response.status_code, stream.url)
            return Reading(Kind.HTTP_ERROR, response.status_code), None
        try:
            text = parse_manifest(response.content, key)
        except VibixProtocolError as exc:
            return Reading(Kind.NOT_PLAYER, 200, message=str(exc)), None
        return Reading(Kind.PLAYABLE, 200, (stream.quality,), files=((stream.quality, stream.url),)), text

    @staticmethod
    def video_of(url: str) -> str:
        """Empty: `vibix=<season>/<position>` is a place in the catalog of its page, and two pages number theirs apart."""
        return ""

    @staticmethod
    def owns(url: str) -> bool:
        """A candidate link of this player: a page address with the catalog entry named in its fragment."""
        return parse_link(url) is not None

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        try:
            return await self.resolve(url, http, max_height)
        except VibixFailure as exc:
            raise StreamUnavailable(exc.reason, transient=exc.transient) from exc

    async def resolve(self, url: str, http: httpx.AsyncClient, max_height: int | None = None) -> dict:
        """Candidate link -> a local manifest with absolute addresses of the best stream within the cap (the best of
        all without one); a page is no stream, so a failure is explicit."""
        target = parse_link(url)
        if target is None:
            raise VibixFailure(reason("player_failed", player="Vibix", cause=reason("link_no_episode")))
        season, position = target
        found = re.search(r"/release/([^/?#]+)", url)
        slug = found.group(1) if found else "vibix"
        try:
            playlist, key = await self.playlist(url)
        except VibixUnavailable as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", url, exc)
            raise VibixFailure(reason("player_failed", player="Vibix", cause=str(exc)), transient=True) from exc
        except VibixProtocolError as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", url, exc)
            raise VibixFailure(reason("player_failed", player="Vibix", cause=str(exc))) from exc
        episode = entry_of(playlist, season, position)
        if episode is None or not episode.streams:
            logger.warning("Entry %s of season %s is missing from the Vibix playlist", position, season)
            raise VibixFailure(reason("player_failed", player="Vibix", cause=reason("vibix_no_episode")))
        failure: VibixFailure | None = None
        for stream in within_cap(episode.streams, max_height):
            reading, text = await self._manifest(stream, key, http)
            if text is not None:
                path = self._manifest_path(slug, season, position, stream.quality)
                try:
                    await storage.call(self._write_manifest, path, text, stream.url, owns=str(path))
                except OSError as exc:
                    logger.warning("Vibix manifest %s was not written: %s", path, exc)
                    raise VibixFailure(reason("player_failed", player="Vibix",
                                              cause=reason("storage_failed", cause=str(exc))), transient=True) from exc
                return {"stream_url": f"file://{path}", "headers": {"Referer": "https://1.ruchime.org/"}}
            failure = _failed(f"Vibix {stream.quality}p", reading)
        raise failure or VibixFailure(reason("vibix_no_streams"))

    def _manifest_path(self, slug: str, season: int, position: int, quality: int) -> Path:
        """An absolute path: ffmpeg is given the manifest as `file://`, whatever its working directory is."""
        return self._manifest_dir.absolute() / "vibix" / f"{slug}-s{season}-{position:04d}-{quality}.m3u8"

    @staticmethod
    def _write_manifest(path: Path, text: str, base_url: str) -> None:
        """Decode segment references to absolute URLs so ffmpeg reads a local file; blocking, so a storage call."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(_absolute_url(base_url, stripped))
            elif stripped.startswith("#EXT-X-KEY") or stripped.startswith("#EXT-X-MAP"):
                lines.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{_absolute_url(base_url, m.group(1))}"', stripped))
            else:
                lines.append(line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
