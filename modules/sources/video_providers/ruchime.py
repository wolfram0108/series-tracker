"""Video provider for Ruchime (https://1.ruchime.org).

Players are probed in the fixed priority order VK -> AllVideo -> Vibix. A player
without a playable stream never becomes a candidate; Vibix needs one browser
capture for the whole series and is resolved again right before download.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from core.reasons import describe, reason

from .base import BaseVideoProvider, StreamUnavailable
from .players import (PLAYER_ORDER, AllVideoPlayer, PlayerAdapter, PlayerRef, VibixPlayer)
from .ranges import episode_ranges
from .ruchime_address import Choice, address_of, choice_of, parse_vibix_link, vibix_link
from .vibix import (Episode, KeyStore, Stream, VibixProtocolError,
                    VibixUnavailable, capture_with_browser, decode_playlist,
                    parse_manifest, stream_expires)

logger = logging.getLogger("sources.video.ruchime")

# Bound concurrent HTTP requests to respect host limits and prevent connection pool saturation.
MAX_CONCURRENT_PROBES = 4
PAGE_RETRY_ATTEMPTS = 4
PAGE_INTERVAL_S = 0.6
PROBE_INTERVAL_S = 0.12
PAGE_COOLDOWN_S = 60.0
PAGE_PAUSE_S = 0.05
RESOLUTION_CEILING = 2160
_PRIORITY = {adapter.name: index for index, adapter in enumerate(PLAYER_ORDER)}
VIBIX_REFRESH_MARGIN_S = 600
REQUEST_TIMEOUT = 8.0

_MONTHS = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "мая": "05",
    "июн": "06", "июл": "07", "авг": "08", "сен": "09", "окт": "10",
    "ноя": "11", "дек": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}

_EPISODE_IN_URL = re.compile(r"/episode-(\d+)")
_ABSOLUTE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class _ManifestUnavailable(VibixUnavailable):
    """A Vibix stream manifest cannot be read; `transient` marks retryable causes."""

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


async def _get_with_retry(http: httpx.AsyncClient, url: str, headers: dict | None = None,
                          max_retries: int = 3, timeout: float = 15.0) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = await http.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code == 200:
                return resp
            if attempt == max_retries - 1:
                return resp
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


@dataclass
class _Episode:
    number: int
    page_url: str
    title: str
    publication_date: str
    refs: dict[str, PlayerRef] = field(default_factory=dict)
    candidates: list[tuple[int, str, str]] = field(default_factory=list)
    player_reasons: dict[str, str] = field(default_factory=dict)
    video_url: str | None = None
    resolution: int | None = None
    source: str | None = None
    reason: str = ""
    page_failed: bool = False
    unverified: bool = False

    @property
    def best_resolution(self) -> int:
        return max((resolution for resolution, _source, _url in self.candidates),
                   default=0)


class RuchimeProvider(BaseVideoProvider):
    name = "ruchime"
    domains = ("1.ruchime.org", "ruchime.org")

    def __init__(self, browser_getter=None, key_path: str | Path | None = None,
                 manifest_dir: str | Path | None = None) -> None:
        self._browser_getter = browser_getter
        self._key_store = KeyStore(Path(key_path) if key_path else None)
        self._manifest_dir = Path(manifest_dir) if manifest_dir else None
        self._vibix_cache: dict[str, tuple[int, list[Episode], bytes]] = {}
        self._vibix_locks: dict[str, asyncio.Lock] = {}
        self._cooldown_until = 0.0
        self._page_next_at = 0.0
        self._page_gate = asyncio.Lock()
        self._probe_next_at = 0.0
        self._probe_gate = asyncio.Lock()

    def configure(self, *, browser_getter=None, key_path: str | Path | None = None,
                  manifest_dir: str | Path | None = None) -> None:
        """Late wiring from the sources module (browser pool and cache paths)."""
        if browser_getter is not None:
            self._browser_getter = browser_getter
        if key_path is not None:
            self._key_store = KeyStore(Path(key_path))
        if manifest_dir is not None:
            self._manifest_dir = Path(manifest_dir)

    def match_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            return host in self.domains and "/release/" in parsed.path
        except Exception:
            return False

    def _extract_slug(self, url: str) -> str:
        path = urlparse(url).path
        match = re.search(r"/release/([^/?#]+)", path)
        if not match:
            raise ValueError(f"Failed to extract release slug from URL: {url}")
        return match.group(1)

    def _page_url(self, url: str) -> str:
        return f"https://1.ruchime.org/release/{self._extract_slug(url)}"

    def canonical_url(self, url: str) -> str:
        return address_of(self._page_url(url), choice_of(url))

    @staticmethod
    def _parse_date(raw_date: str | None) -> str:
        # Deterministic date parsing to keep media_unique_id stable across scans.
        default_date = "1970-01-01T12:00:00Z"
        if not raw_date:
            return default_date
        parts = raw_date.strip().split()
        if len(parts) == 3:
            day, mon, year = parts
            month = _MONTHS.get(mon.lower()[:3])
            if month and day.isdigit() and year.isdigit():
                return f"{year}-{month}-{day.zfill(2)}T12:00:00Z"
        return default_date

    @staticmethod
    def _episode_title(series_name: str, ep: dict) -> str:
        num = ep.get("number")
        c_from = ep.get("compilation_from")
        c_to = ep.get("compilation_to")
        if c_from is not None and c_to is not None:
            label = f"{c_from}-{c_to} серия"
        else:
            label = f"{num} серия"
        return f"{series_name} - {label}"

    @staticmethod
    def _extract_allvideo_resolution(html_text: str) -> int | None:
        return AllVideoPlayer.extract_resolution(html_text)

    @staticmethod
    def _extract_best_stream_url(file_val: str) -> str | None:
        return AllVideoPlayer.best_stream_url(file_val)

    async def _read_release(self, url: str, http: httpx.AsyncClient,
                            headers: dict) -> tuple[httpx.Response, str]:
        slug = self._extract_slug(url)
        canonical = f"https://1.ruchime.org/release/{slug}"
        try:
            resp = await _get_with_retry(http, canonical, headers=headers)
        except Exception:
            resp = await _get_with_retry(http, url, headers=headers)
        if resp.status_code != 200:
            resp = await _get_with_retry(http, url, headers=headers)
        resp.raise_for_status()
        return resp, slug

    async def _wait_page_slot(self) -> None:
        """Keep episode-page requests under the site's burst limit."""
        async with self._page_gate:
            delay = self._page_next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._page_next_at = time.monotonic() + PAGE_INTERVAL_S

    async def _wait_probe_slot(self) -> None:
        """Spread player probes so a burst is not read as a missing source."""
        async with self._probe_gate:
            delay = self._probe_next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._probe_next_at = time.monotonic() + PROBE_INTERVAL_S

    async def _fetch_episode_page(self, http: httpx.AsyncClient, url: str,
                                  headers: dict,
                                  sem: asyncio.Semaphore) -> httpx.Response | None:
        """Fetch one episode page with cooldown-aware retries for 429/5xx."""
        response: httpx.Response | None = None
        for attempt in range(PAGE_RETRY_ATTEMPTS):
            delay = self._cooldown_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._wait_page_slot()
            async with sem:
                try:
                    response = await http.get(url, headers=headers,
                                              follow_redirects=True,
                                              timeout=REQUEST_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 - retried below
                    logger.warning("Episode page %s attempt %d failed: %s",
                                   url, attempt + 1, exc)
                    response = None
                await asyncio.sleep(PAGE_PAUSE_S)
            if response is not None and response.status_code == 200:
                return response
            if response is not None and response.status_code == 429:
                retry_after = _retry_after(response) or PAGE_COOLDOWN_S * (attempt + 1)
                self._cooldown_until = max(self._cooldown_until,
                                           time.monotonic() + retry_after)
                logger.warning("Episode page %s rate-limited; cooling down %.0f s",
                               url, retry_after)
                continue
            if response is not None and response.status_code < 500:
                return response
        return response

    async def _read_episode(self, slug: str, series_name: str, ep: dict,
                            http: httpx.AsyncClient, headers: dict,
                            sem: asyncio.Semaphore,
                            first_adapter: PlayerAdapter | None = None
                            ) -> _Episode | None:
        """Fetch one episode page, record its players and probe the first one."""
        num = ep.get("number")
        if num is None:
            return None
        page_url = f"https://1.ruchime.org/release/{slug}/episode-{num}"
        info = _Episode(number=int(num), page_url=page_url,
                        title=self._episode_title(series_name, ep),
                        publication_date=self._parse_date(ep.get("date")))
        response = await self._fetch_episode_page(http, page_url, headers, sem)
        if response is None:
            info.page_failed = True
            info.reason = reason("page_unreachable")
            return info
        if response.status_code != 200:
            info.page_failed = True
            info.reason = reason("page_status", status=response.status_code)
            return info
        text = html.unescape(response.text)
        for adapter in PLAYER_ORDER:
            ref = adapter.find(text, page_url)
            if ref is not None:
                info.refs[adapter.name] = ref
        if first_adapter is not None and first_adapter.name in info.refs:
            await self._wait_probe_slot()
            probe = await first_adapter.probe(info.refs[first_adapter.name], http, page_url)
            if probe.available:
                info.candidates.append((probe.resolution or 0, first_adapter.name,
                                        probe.ref.url))
            else:
                info.reason = probe.reason
                info.player_reasons[first_adapter.name] = probe.reason
                if probe.transient:
                    info.unverified = True
        return info

    @staticmethod
    def _needs_lower_source(episodes: list[_Episode], adapter_name: str) -> list[_Episode]:
        """Check lower sources while a higher resolution is still possible."""
        return [item for item in episodes
                if adapter_name in item.refs
                and item.best_resolution < RESOLUTION_CEILING]

    async def _apply_adapter(self, episodes: list[_Episode], adapter: PlayerAdapter,
                             http: httpx.AsyncClient, every: bool = False) -> None:
        """Probe one player where it can improve on the current best resolution; `every` probes all its episodes."""
        targets = ([item for item in episodes if adapter.name in item.refs] if every
                   else self._needs_lower_source(episodes, adapter.name))
        if not targets:
            return
        async def probe_one(item: _Episode):
            await self._wait_probe_slot()
            return await adapter.probe(item.refs[adapter.name], http, item.page_url)

        probes = await asyncio.gather(*(probe_one(item) for item in targets),
                                      return_exceptions=True)
        for item, probe in zip(targets, probes):
            if isinstance(probe, Exception):
                item.reason = reason("player_failed", player=adapter.name, cause=str(probe))
                item.player_reasons[adapter.name] = item.reason
                logger.warning("%s probe failed for %s: %s",
                               adapter.name, item.page_url, probe)
                continue
            if probe.available:
                item.candidates.append((probe.resolution or 0, adapter.name,
                                        probe.ref.url))
            else:
                item.reason = probe.reason
                item.player_reasons[adapter.name] = probe.reason
                if probe.transient:
                    item.unverified = True

    @staticmethod
    def _vibix_season(playlist: list[Episode], choice: Choice) -> tuple[int | None, str]:
        """Position of the season the series takes from, or a coded refusal: several seasons need a choice."""
        seasons = sorted({episode.season_index for episode in playlist})
        if choice.vibix_season is not None:
            if choice.vibix_season in seasons:
                return choice.vibix_season, ""
            return None, reason("vibix_season_missing", season=choice.vibix_season)
        if len(seasons) == 1:
            return seasons[0], ""
        return None, reason("vibix_season_required", seasons=len(seasons))

    async def _apply_vibix(self, episodes: list[_Episode], http: httpx.AsyncClient,
                           choice: Choice) -> None:
        """Resolve the series playlist once and map episodes by their number within the chosen season."""
        targets = self._needs_lower_source(episodes, "vibix")
        if not targets:
            return
        try:
            playlist, key = await self._vibix_playlist(targets[0].page_url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix playlist unavailable for %s: %s",
                           targets[0].page_url, exc)
            for item in targets:
                item.reason = reason("player_failed", player="Vibix", cause=str(exc))
                item.player_reasons["vibix"] = str(exc)
            return

        position, refusal = self._vibix_season(playlist, choice)
        if position is None:
            for item in targets:
                item.player_reasons["vibix"] = refusal
                if not item.candidates:
                    item.reason = refusal
            return

        by_number = {episode.number: episode for episode in playlist
                     if episode.season_index == position}
        to_probe: list[tuple[_Episode, Episode, int]] = []
        for item in targets:
            episode = by_number.get(item.number)
            if episode is None or not episode.streams:
                item.player_reasons["vibix"] = reason("vibix_no_episode")
                if not item.candidates:
                    item.reason = reason("vibix_no_episode")
                continue
            declared = max(stream.quality for stream in episode.streams)
            floor = item.best_resolution
            if declared <= floor:
                # Cannot outrank a better source, so a manifest request is wasted.
                item.candidates.append((declared, "vibix", vibix_link(item.page_url, position, item.number)))
                continue
            to_probe.append((item, episode, floor))

        async def probe_one(episode: Episode, floor: int):
            await self._wait_probe_slot()
            return await self._live_vibix_stream(episode, key, http, floor)

        results = await asyncio.gather(
            *(probe_one(episode, floor) for _item, episode, floor in to_probe),
            return_exceptions=True)
        for (item, _episode, _floor), outcome in zip(to_probe, results):
            if isinstance(outcome, _ManifestUnavailable):
                item.player_reasons["vibix"] = str(outcome)
                if not item.candidates:
                    item.reason = str(outcome)
                if outcome.transient:
                    item.unverified = True
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            stream, _manifest = outcome
            item.candidates.append((stream.quality, "vibix", vibix_link(item.page_url, position, item.number)))

    async def _read_vibix_manifest(self, stream: Stream, key: bytes,
                                   http: httpx.AsyncClient) -> str:
        """Fetch and decode one stream manifest; the CDN may announce streams it cannot serve."""
        label = f"Vibix {stream.quality}p"
        try:
            response = await http.get(stream.url, follow_redirects=True, timeout=15.0)
        except httpx.HTTPError as exc:
            raise _ManifestUnavailable(reason("manifest_failed", label=label, error=type(exc).__name__),
                                       transient=True) from exc
        status = response.status_code
        if status != 200:
            logger.warning("Vibix manifest HTTP %s for %s", status, stream.url)
            raise _ManifestUnavailable(
                reason("manifest_status", label=label, status=status),
                transient=status in (408, 425, 429) or status >= 500)
        try:
            return parse_manifest(response.content, key)
        except VibixProtocolError as exc:
            raise _ManifestUnavailable(reason("manifest_undecoded", label=label, error=str(exc))) from exc

    async def _live_vibix_stream(self, episode: Episode, key: bytes,
                                 http: httpx.AsyncClient,
                                 floor: int) -> tuple[Stream, str]:
        """Best stream above `floor` whose manifest is readable, with the manifest text."""
        failure: _ManifestUnavailable | None = None
        for stream in sorted(episode.streams, key=lambda item: item.quality,
                             reverse=True):
            if stream.quality <= floor:
                break
            try:
                return stream, await self._read_vibix_manifest(stream, key, http)
            except _ManifestUnavailable as exc:
                failure = exc
        raise failure or _ManifestUnavailable(reason("vibix_no_streams"))

    @staticmethod
    def _availability_report(episodes: list[_Episode], off: frozenset[str] = frozenset()) -> dict:
        """Aggregate per-player availability for the series status; a disabled player is never counted."""
        players: dict[str, dict] = {}
        for adapter in PLAYER_ORDER:
            if adapter.name in off:
                players[adapter.name] = {
                    "present": sum(1 for item in episodes if adapter.name in item.refs),
                    "available": 0, "selected": 0, "unverified": 0, "max_resolution": None,
                    "reason": reason("source_disabled"),
                }
                continue
            present = [item for item in episodes if adapter.name in item.refs]
            available = [item for item in episodes
                         if any(source == adapter.name
                                for _resolution, source, _url in item.candidates)]
            available_numbers = {item.number for item in available}
            selected = [item for item in episodes if item.source == adapter.name]
            reasons = Counter(item.player_reasons.get(adapter.name)
                              for item in present
                              if item.number not in available_numbers)
            reasons.pop(None, None)
            players[adapter.name] = {
                "present": len(present),
                "available": len(available),
                "selected": len(selected),
                "unverified": sum(1 for item in present if item.unverified),
                "max_resolution": max(
                    (item.resolution for item in selected if item.resolution),
                    default=None),
                "reason": reasons.most_common(1)[0][0] if reasons else "",
            }
        holes = [{"episode": item.number, "reason": item.reason,
                  "unverified": item.unverified}
                 for item in episodes if not item.candidates]
        return {
            "episodes": len(episodes),
            "sources": dict(Counter(item.source for item in episodes if item.source)),
            "players": players,
            "holes": holes,
        }

    async def _vibix_playlist(self, page_url: str) -> tuple[list[Episode], bytes]:
        """Return the cached series playlist; capture it once per signature window."""
        page_url = urldefrag(page_url).url
        slug = self._extract_slug(page_url)
        lock = self._vibix_locks.setdefault(slug, asyncio.Lock())
        async with lock:
            now = int(time.time())
            cached = self._vibix_cache.get(slug)
            if cached is not None and cached[0] - now > VIBIX_REFRESH_MARGIN_S:
                return cached[1], cached[2]
            if self._browser_getter is None:
                raise VibixUnavailable(reason("vibix_no_browser_pool"))
            browser = await self._browser_getter()
            capture = await capture_with_browser(browser, page_url)
            key, _ = self._key_store.resolve(api_body=capture.body,
                                             manifest_body=capture.manifest_body)
            episodes = decode_playlist(capture.body, key)
            if not episodes:
                raise VibixProtocolError("Vibix API returned an empty playlist")
            expiries = [stream_expires(stream.url)
                        for episode in episodes for stream in episode.streams]
            expiries = [value for value in expiries if value is not None]
            expires = min(expiries) if expiries else now + 3600
            self._vibix_cache[slug] = (expires, episodes, key)
            return episodes, key

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Read only the release page; probe its preview players for a quick report."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._read_release(url, http, headers)
        raw_text = resp.text

        name_ru, name_en = self._release_names(raw_text, slug, url)
        episodes_raw = self._release_episodes(raw_text)

        page_url, choice = self._page_url(url), choice_of(url)
        report, _preview_resolution = await self._quick_report(raw_text, page_url, http)
        for name, entry in report.items():
            entry["enabled"] = name not in choice.off
            entry["toggle"] = address_of(page_url, choice.toggled(name))
        episodes = [
            {"number": ep.get("number"),
             "title": self._episode_title(name_ru, ep),
             "date": ep.get("date")}
            for ep in episodes_raw if ep.get("number") is not None]
        titles = [episode["title"] for episode in episodes]
        # Per-episode quality is a fact from the scan, not a release preview.
        videos = [{"title": title, "resolution": None} for title in titles]

        return {
            "canonical_url": address_of(page_url, choice),
            "title": {"ru": name_ru, "en": name_en},
            "titles": titles,
            "episodes": episodes,
            # Quality is a per-episode scan fact, not a release preview.
            "resolution": None,
            "videos": videos,
            "episodes_count": len(titles),
            "provider": self.name,
            "source_type": "web_video",
            "releases": [],
            "players": report,
        }

    has_layout = True

    async def layout(self, url: str, http: httpx.AsyncClient) -> dict:
        """The Vibix catalog as sources, one per season, and the season the address chose.

        The catalog is read through a browser, so it is asked for apart from the release parse."""
        choice = choice_of(url)
        empty = {"sources": [], "selected_source": None}
        if "vibix" in choice.off:
            return {**empty, "reason": ""}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._read_release(url, http, headers)
        numbers = sorted(int(ep["number"]) for ep in self._release_episodes(resp.text)
                         if ep.get("number") is not None)
        if not numbers:
            return {**empty, "reason": reason("title_no_videos", player="Vibix")}
        episode_url = f"https://1.ruchime.org/release/{slug}/episode-{numbers[0]}"
        page = await self._fetch_episode_page(http, episode_url, headers, asyncio.Semaphore(1))
        if page is None or page.status_code != 200:
            return {**empty, "reason": reason("page_unreachable")}
        if VibixPlayer().find(html.unescape(page.text), episode_url) is None:
            return {**empty, "reason": reason("page_no_player", player="vibix")}
        try:
            playlist, _key = await self._vibix_playlist(episode_url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix catalog unavailable for %s: %s", episode_url, exc)
            return {**empty, "reason": reason("player_failed", player="Vibix", cause=str(exc))}
        sources = self._vibix_sources(playlist, self._page_url(url), choice)
        position, _refusal = self._vibix_season(playlist, choice)
        selected = address_of(self._page_url(url), choice.with_season(position)) if position else None
        return {"sources": sources, "selected_source": selected, "reason": ""}

    async def coverage(self, url: str, http: httpx.AsyncClient,
                       progress: Callable[[int, int, str], None] | None = None) -> dict:
        """What each page player really gives: its episodes and the resolution of each, grouped by ranges.

        Every episode page is read and every player is probed, which is slow, so the add form asks for it
        apart from the parse and shows the progress. The choice of the address does not change the answer:
        it is read once per page, and a disabled player is only hidden by the form."""
        probes = [adapter for adapter in PLAYER_ORDER if not adapter.series_scope]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._read_release(url, http, headers)
        listed = self._release_episodes(resp.text)
        if not listed:
            return {"players": {}, "reason": reason("title_no_videos", player="Ruchime")}
        try:
            episodes = await self._survey(slug, listed, http, headers, probes, progress)
        except ValueError as exc:
            logger.warning("Coverage of %s not read: %s", slug, exc)
            return {"players": {}, "reason": str(exc)}
        return {"players": self._coverage(episodes, probes), "reason": ""}

    async def _survey(self, slug: str, listed: list[dict], http: httpx.AsyncClient, headers: dict,
                      probes: list[PlayerAdapter], progress: Callable[[int, int, str], None] | None) -> list[_Episode]:
        """Read every episode page, then probe every page player on every episode that shows it."""
        sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
        total, done, last_emit = len(listed), 0, 0.0

        async def read_one(ep: dict) -> _Episode | None:
            nonlocal done, last_emit
            info = await self._read_episode(slug, slug, ep, http, headers, sem)
            done += 1
            now = time.monotonic()
            if progress is not None and (done == total or now - last_emit >= 0.5):
                last_emit = now
                progress(done, total, "pages")
            return info

        read = await asyncio.gather(*(read_one(ep) for ep in listed))
        episodes = [item for item in read if item is not None]
        failed = [item for item in episodes if item.page_failed]
        if failed:
            raise ValueError(reason("pages_unread", site="Ruchime", count=len(failed), total=len(episodes)))
        if progress is not None:
            progress(total, total, "players")
        for adapter in probes:
            await self._apply_adapter(episodes, adapter, http, every=True)
        return episodes

    @staticmethod
    def _coverage(episodes: list[_Episode], probes: list[PlayerAdapter]) -> dict[str, dict]:
        """Per player: episodes that show it, episodes it plays, and their resolution grouped by ranges."""
        players: dict[str, dict] = {}
        for adapter in probes:
            by_resolution: dict[int, set[int]] = {}
            for item in episodes:
                for resolution, source, _url in item.candidates:
                    if source == adapter.name:
                        by_resolution.setdefault(resolution, set()).add(item.number)
            numbers = sorted(set().union(*by_resolution.values())) if by_resolution else []
            players[adapter.name] = {
                "present": sum(1 for item in episodes if adapter.name in item.refs),
                "available": len(numbers), "count": len(numbers), "ranges": episode_ranges(numbers),
                "resolutions": [{"resolution": height, "ranges": episode_ranges(by_resolution[height]),
                                 "count": len(by_resolution[height])}
                                for height in sorted(by_resolution, reverse=True)],
            }
        return players

    @staticmethod
    def _vibix_sources(playlist: list[Episode], page_url: str, choice: Choice) -> list[dict]:
        """One source per season of the catalog: the episodes it really has, and their best resolution by ranges."""
        sources = []
        for position in sorted({episode.season_index for episode in playlist}):
            members = [episode for episode in playlist
                       if episode.season_index == position and episode.streams]
            if not members:
                continue
            numbers = sorted({episode.number for episode in members})
            by_resolution: dict[int, set[int]] = {}
            for episode in members:
                best = max(stream.quality for stream in episode.streams)
                by_resolution.setdefault(best, set()).add(episode.number)
            sources.append({
                "url": address_of(page_url, choice.with_season(position)),
                "player": "vibix", "studio": members[0].season, "kind": "",
                "first": numbers[0], "last": numbers[-1], "count": len(numbers),
                "ranges": episode_ranges(numbers), "episodes": numbers,
                "resolutions": [{"resolution": height, "ranges": episode_ranges(by_resolution[height]),
                                 "count": len(by_resolution[height])}
                                for height in sorted(by_resolution, reverse=True)],
            })
        return sources

    @staticmethod
    def _release_names(raw_text: str, slug: str, url: str) -> tuple[str | None, str | None]:
        name_ru: str | None = None
        name_en: str | None = None
        for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                                 raw_text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
            except Exception:
                continue
            if data.get("@type") == "TVSeries":
                name_ru = data.get("name")
                name_en = data.get("alternateName")
                break
        if not name_ru:
            title = re.search(r"<title>(.*?)(?:—\s*Ruchime.*)?</title>", raw_text)
            if title:
                name_ru = re.sub(r"\s+\d+\s+серия.*", "", title.group(1)).strip()
            else:
                name_ru = slug.replace("-", " ").title()
        return name_ru, name_en

    @staticmethod
    def _release_episodes(raw_text: str) -> list[dict]:
        match = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", raw_text, re.DOTALL)
        if not match:
            return []
        try:
            episodes = json.loads(match.group(1))
        except Exception as exc:
            logger.warning("Failed parsing ALL_EPS JSON: %s", exc)
            return []
        return episodes if isinstance(episodes, list) else []

    async def _quick_report(self, raw_text: str, page_url: str,
                            http: httpx.AsyncClient) -> tuple[dict, int | None]:
        """Probe the page's preview players; Vibix is checked on scan."""
        text = html.unescape(raw_text)
        refs = {adapter.name: adapter.find(text, page_url) for adapter in PLAYER_ORDER}
        tasks = []
        adapters: list[PlayerAdapter] = []
        for adapter in PLAYER_ORDER:
            if adapter.series_scope or refs[adapter.name] is None:
                continue
            adapters.append(adapter)
            tasks.append(adapter.probe(refs[adapter.name], http, page_url))
        probes = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        report: dict[str, dict] = {}
        resolutions: list[int] = []
        for adapter in PLAYER_ORDER:
            ref = refs[adapter.name]
            if adapter.series_scope:
                report[adapter.name] = {
                    "present": ref is not None,
                    "available": None,
                    "resolution": None,
                    "reason": reason("checked_on_scan"),
                }
                continue
            if ref is None:
                report[adapter.name] = {"present": False, "available": False,
                                        "resolution": None, "reason": reason("page_no_player", player=adapter.name)}
                continue
            probe = probes[adapters.index(adapter)] if tasks else None
            if isinstance(probe, Exception) or probe is None:
                report[adapter.name] = {"present": True, "available": False,
                                        "resolution": None,
                                        "reason": reason("probe_failed", error=str(probe))}
                continue
            report[adapter.name] = {
                "present": True,
                "available": probe.available,
                "resolution": probe.resolution,
                "reason": probe.reason,
            }
            if probe.available and probe.resolution is not None:
                resolutions.append(probe.resolution)
        return report, (max(resolutions) if resolutions else None)

    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict,
                            episodes: list[int] | None = None,
                            progress: Callable[[int, int, str], None] | None = None) -> dict:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._read_release(url, http, headers)

        choice = choice_of(url)
        active = [adapter for adapter in PLAYER_ORDER if adapter.name not in choice.off]
        if not active:
            return {"videos": [], "players": self._availability_report([], choice.off)}
        # Vibix has no per-page probe: it is resolved later for the whole series.
        probed_first = active[0] if not active[0].series_scope else None
        later = active[1:] if probed_first else active

        all_episodes = self._release_episodes(resp.text)
        if not all_episodes:
            raise ValueError(f"Ruchime: array ALL_EPS not found on page {url}")
        if episodes is not None:
            allowed = set(episodes)
            episodes_raw = [ep for ep in all_episodes if ep.get("number") in allowed]
        else:
            episodes_raw = all_episodes

        name_ru = series.get("name")
        if not name_ru:
            match = re.search(r'"name":"([^"]+)"', resp.text)
            name_ru = match.group(1) if match else slug

        sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
        total = len(episodes_raw)
        done = 0
        last_emit = 0.0
        counter_lock = asyncio.Lock()

        async def read_one(ep: dict) -> _Episode | None:
            nonlocal done, last_emit
            info = await self._read_episode(slug, name_ru, ep, http, headers, sem,
                                            first_adapter=probed_first)
            async with counter_lock:
                done += 1
                now = time.monotonic()
                if progress is not None and (done == total or now - last_emit >= 0.5):
                    last_emit = now
                    progress(done, total, "pages")
            return info

        resolved = await asyncio.gather(*(read_one(ep) for ep in episodes_raw))
        if progress is not None:
            progress(total, total, "players")
        episodes = [item for item in resolved if item is not None]

        failed = [item for item in episodes if item.page_failed]
        if failed:
            raise ValueError(reason("pages_unread", site="Ruchime", count=len(failed), total=len(episodes)))

        for adapter in later:
            if episodes and all(item.best_resolution >= RESOLUTION_CEILING
                                for item in episodes):
                break
            if adapter.series_scope:
                await self._apply_vibix(episodes, http, choice)
            else:
                await self._apply_adapter(episodes, adapter, http)

        facts: list[dict] = []
        for item in episodes:
            if not item.candidates:
                if item.unverified:
                    logger.warning("Skipping episode %s: source not confirmed (%s)",
                                   item.page_url, describe(item.reason) or "no data")
                else:
                    logger.warning("Skipping episode %s: no playable player (%s)",
                                   item.page_url, describe(item.reason) or "no source")
                continue
            resolution, source, url = max(
                item.candidates,
                key=lambda candidate: (candidate[0], -_PRIORITY[candidate[1]]))
            item.video_url, item.resolution, item.source = url, resolution, source
            download_url = None
            if source == "vk" and "vk" in item.refs:
                download_url = item.refs["vk"].raw_url or None
            facts.append({
                "title": item.title,
                "url": item.video_url,
                "download_url": download_url,
                "publication_date": item.publication_date,
                "resolution": item.resolution,
            })

        facts.sort(key=lambda fact: fact["publication_date"])
        report = self._availability_report(episodes, choice.off)
        logger.info("Ruchime scan %s: серий %d, источники %s",
                    slug, len(facts), report["sources"])
        if progress is not None:
            progress(total, total, "done")
        return {"videos": facts, "players": report}

    async def resolve_stream(self, url: str, http: httpx.AsyncClient,
                             max_height: int | None = None) -> dict | None:
        """Resolve VK embeds, AllVideo embeds and Vibix episode references."""
        if "video_ext.php" in url and ("vk.com" in url or "vkvideo.ru" in url):
            return await self._resolve_vk(url, http)
        if any(domain in url for domain in ("fsst.online", "incvideo1.online")):
            return await self._resolve_allvideo(url, http)
        if self.match_url(url) and _EPISODE_IN_URL.search(url):
            return await self._resolve_vibix(url, http)
        return None

    async def _resolve_vk(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Fresh direct stream from the VK embed page.

        The canonical `vk.com/video...` page is rejected by yt-dlp as
        «Access restricted» for some videos, while the embed page with hash
        still exposes signed mp4 URLs. Resolve them at download time so the
        signature is fresh.
        """
        try:
            response = await http.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                         "Referer": "https://vk.com/"},
                follow_redirects=True, timeout=15.0)
            if response.status_code != 200:
                logger.warning("VK embed HTTP %s for %s",
                               response.status_code, url)
                return None
            streams = [
                (int(quality), html.unescape(raw.replace("\\/", "/")))
                for quality, raw in re.findall(r'"mp4_(\d+)":"([^"]+)"',
                                                response.text)
            ]
            if streams:
                _quality, stream_url = max(streams, key=lambda item: item[0])
                return {"stream_url": stream_url,
                        "headers": {"Referer": "https://vk.com/"}}
            hls = re.search(r'"hls":"([^"]+)"', response.text)
            if hls:
                stream_url = html.unescape(hls.group(1).replace("\\/", "/"))
                return {"stream_url": stream_url,
                        "headers": {"Referer": "https://vk.com/"}}
            logger.warning("VK embed has no playable stream: %s", url)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed resolving VK stream for %s: %s", url, exc)
            return None

    async def _resolve_allvideo(self, url: str, http: httpx.AsyncClient) -> dict | None:
        try:
            response = await http.get(url, headers={"Referer": "https://1.ruchime.org/"},
                                      follow_redirects=True)
            if response.status_code != 200:
                return None
            match = re.search(r'file:\s*["\']([^"\']+)["\']', response.text)
            if not match:
                return None
            stream_url = self._extract_best_stream_url(match.group(1))
            if not stream_url:
                return None
            final_url = str(getattr(response, "url", "") or url)
            parsed = urlparse(final_url)
            if not parsed.scheme or not parsed.netloc:
                parsed = urlparse(url)
            origin = (f"{parsed.scheme}://{parsed.netloc}/"
                      if parsed.scheme and parsed.netloc else "")
            return {"stream_url": stream_url,
                    "headers": {"Referer": origin} if origin else {}}
        except Exception as exc:  # noqa: BLE001 - resolution is retried by download
            logger.warning("Failed resolving AllVideo stream for %s: %s", url, exc)
            return None

    async def _resolve_vibix(self, url: str, http: httpx.AsyncClient) -> dict:
        """Episode page URL -> local manifest; a page is no stream, so failure is explicit."""
        match = _EPISODE_IN_URL.search(url)
        if not match:
            raise StreamUnavailable(reason("player_failed", player="Vibix", cause=reason("link_no_episode")))
        number = int(match.group(1))
        try:
            playlist, key = await self._vibix_playlist(url)
        except VibixUnavailable as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", url, exc)
            raise StreamUnavailable(reason("player_failed", player="Vibix", cause=str(exc)), transient=True) from exc
        except VibixProtocolError as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", url, exc)
            raise StreamUnavailable(reason("player_failed", player="Vibix", cause=str(exc))) from exc
        target = parse_vibix_link(url)
        if target is not None:
            position, number = target
        else:
            # A link from before the choice existed: only a single-season catalog leaves no doubt.
            position, refusal = self._vibix_season(playlist, Choice())
            if position is None:
                raise StreamUnavailable(reason("player_failed", player="Vibix", cause=refusal))
        episode = next((item for item in playlist
                        if item.season_index == position and item.number == number), None)
        if episode is None or not episode.streams:
            logger.warning("Episode %s of season %s is missing from the Vibix playlist", number, position)
            raise StreamUnavailable(reason("player_failed", player="Vibix", cause=reason("vibix_no_episode")))
        try:
            stream, text = await self._live_vibix_stream(episode, key, http, floor=0)
        except _ManifestUnavailable as exc:
            raise StreamUnavailable(str(exc), transient=exc.transient) from exc
        manifest = self._write_manifest(text, stream.url, self._extract_slug(url),
                                        number, stream.quality).resolve()
        return {"stream_url": f"file://{manifest}",
                "headers": {"Referer": "https://1.ruchime.org/"}}

    def _write_manifest(self, text: str, base_url: str, slug: str, number: int,
                        quality: int) -> Path:
        """Decode segment references to absolute URLs so ffmpeg reads a local file."""
        directory = (self._manifest_dir or Path(tempfile.gettempdir())) / "vibix"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{slug}-{number:04d}-{quality}.m3u8"
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(_absolute_url(base_url, stripped))
            elif stripped.startswith("#EXT-X-KEY") or stripped.startswith("#EXT-X-MAP"):
                lines.append(re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{_absolute_url(base_url, m.group(1))}"',
                    stripped))
            else:
                lines.append(line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = response.headers.get("Retry-After")
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


def _absolute_url(base_url: str, reference: str) -> str:
    if _ABSOLUTE_URL.match(reference):
        return reference
    # A colon in the first path segment would be parsed as a URL scheme.
    if ":" in reference.split("/", 1)[0] and not reference.startswith("./"):
        reference = "./" + reference
    return urljoin(base_url, reference)
