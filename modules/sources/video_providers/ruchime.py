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
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .base import BaseVideoProvider
from .players import (PLAYER_ORDER, AllVideoPlayer, PlayerAdapter, PlayerRef)
from .vibix import (Episode, KeyStore, VibixProtocolError, VibixUnavailable,
                    capture_with_browser, decode_playlist, parse_manifest,
                    stream_expires)

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

    def canonical_url(self, url: str) -> str:
        slug = self._extract_slug(url)
        return f"https://1.ruchime.org/release/{slug}"

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
            info.reason = "страница недоступна"
            return info
        if response.status_code != 200:
            info.page_failed = True
            info.reason = f"страница ответила {response.status_code}"
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
        return info

    @staticmethod
    def _needs_lower_source(episodes: list[_Episode], adapter_name: str) -> list[_Episode]:
        """Check lower sources only while a higher resolution is still possible."""
        return [item for item in episodes
                if adapter_name in item.refs
                and item.best_resolution < RESOLUTION_CEILING]

    async def _apply_adapter(self, episodes: list[_Episode], adapter: PlayerAdapter,
                             http: httpx.AsyncClient) -> None:
        """Probe one player where it can improve on the current best resolution."""
        targets = self._needs_lower_source(episodes, adapter.name)
        if not targets:
            return

        async def probe_one(item: _Episode):
            await self._wait_probe_slot()
            return await adapter.probe(item.refs[adapter.name], http, item.page_url)

        probes = await asyncio.gather(*(probe_one(item) for item in targets),
                                      return_exceptions=True)
        for item, probe in zip(targets, probes):
            if isinstance(probe, Exception):
                item.reason = f"{adapter.name}: {probe}"
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

    async def _apply_vibix(self, episodes: list[_Episode],
                           http: httpx.AsyncClient) -> None:
        """Resolve the series playlist once and map episodes by their number."""
        targets = self._needs_lower_source(episodes, "vibix")
        if not targets:
            return
        try:
            playlist, _key = await self._vibix_playlist(targets[0].page_url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix playlist unavailable for %s: %s",
                           targets[0].page_url, exc)
            for item in targets:
                item.reason = f"Vibix: {exc}"
                item.player_reasons["vibix"] = str(exc)
            return

        by_number = {episode.number: episode for episode in playlist}
        for item in targets:
            episode = by_number.get(item.number)
            if episode is None or not episode.streams:
                item.player_reasons["vibix"] = "серии нет в плейлисте Vibix"
                if not item.candidates:
                    item.reason = "серии нет в плейлисте Vibix"
                continue
            resolution = max(stream.quality for stream in episode.streams)
            item.candidates.append((resolution, "vibix", item.page_url))

    @staticmethod
    def _availability_report(episodes: list[_Episode]) -> dict:
        """Aggregate per-player availability for the series status."""
        players: dict[str, dict] = {}
        for adapter in PLAYER_ORDER:
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
                "max_resolution": max(
                    (item.resolution for item in selected if item.resolution),
                    default=None),
                "reason": reasons.most_common(1)[0][0] if reasons else "",
            }
        holes = [{"episode": item.number, "reason": item.reason}
                 for item in episodes if not item.candidates]
        return {
            "episodes": len(episodes),
            "sources": dict(Counter(item.source for item in episodes if item.source)),
            "players": players,
            "holes": holes,
        }

    async def _vibix_playlist(self, page_url: str) -> tuple[list[Episode], bytes]:
        """Return the cached series playlist; capture it once per signature window."""
        slug = self._extract_slug(page_url)
        lock = self._vibix_locks.setdefault(slug, asyncio.Lock())
        async with lock:
            now = int(time.time())
            cached = self._vibix_cache.get(slug)
            if cached is not None and cached[0] - now > VIBIX_REFRESH_MARGIN_S:
                return cached[1], cached[2]
            if self._browser_getter is None:
                raise VibixUnavailable("браузерный пул не подключён")
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

        titles = [self._episode_title(name_ru, ep)
                  for ep in episodes_raw if ep.get("number") is not None]
        report, resolution = await self._quick_report(raw_text, self.canonical_url(url), http)
        last_report, last_resolution = await self._probe_last_episode(
            slug, episodes_raw, http, headers)
        report = _merge_reports(report, last_report)
        resolution = max((value for value in (resolution, last_resolution)
                          if value is not None), default=None)
        videos = [{"title": title, "resolution": resolution} for title in titles]

        return {
            "canonical_url": self.canonical_url(url),
            "title": {"ru": name_ru, "en": name_en},
            "titles": titles,
            "resolution": resolution,
            "videos": videos,
            "episodes_count": len(titles),
            "provider": self.name,
            "source_type": "web_video",
            "releases": [],
            "players": report,
        }

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

    async def _probe_last_episode(self, slug: str, episodes_raw: list[dict],
                                  http: httpx.AsyncClient,
                                  headers: dict) -> tuple[dict, int | None]:
        """Sample the newest episode so the release badge does not lag behind."""
        numbers = [ep.get("number") for ep in episodes_raw
                   if ep.get("number") is not None]
        if not numbers:
            return {}, None
        page_url = f"https://1.ruchime.org/release/{slug}/episode-{max(numbers)}"
        try:
            response = await http.get(page_url, headers=headers,
                                      follow_redirects=True, timeout=REQUEST_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - the badge is best-effort
            logger.warning("Quick probe of the last episode failed: %s", exc)
            return {}, None
        if response.status_code != 200:
            return {}, None
        return await self._quick_report(response.text, page_url, http)

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
                    "reason": "проверяется при сканировании",
                }
                continue
            if ref is None:
                report[adapter.name] = {"present": False, "available": False,
                                        "resolution": None, "reason": "плеера нет на странице"}
                continue
            probe = probes[adapters.index(adapter)] if tasks else None
            if isinstance(probe, Exception) or probe is None:
                report[adapter.name] = {"present": True, "available": False,
                                        "resolution": None,
                                        "reason": f"проба не удалась: {probe}"}
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
                            series: dict) -> list[dict]:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._read_release(url, http, headers)

        episodes_raw = self._release_episodes(resp.text)
        if not episodes_raw:
            raise ValueError(f"Ruchime: array ALL_EPS not found on page {url}")

        name_ru = series.get("name")
        if not name_ru:
            match = re.search(r'"name":"([^"]+)"', resp.text)
            name_ru = match.group(1) if match else slug

        sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
        resolved = await asyncio.gather(
            *(self._read_episode(slug, name_ru, ep, http, headers, sem,
                                 first_adapter=PLAYER_ORDER[0])
              for ep in episodes_raw))
        episodes = [item for item in resolved if item is not None]

        failed = [item for item in episodes if item.page_failed]
        if failed:
            raise ValueError(
                f"Ruchime: не прочитаны страницы {len(failed)} из {len(episodes)} "
                "серий (сайт ограничил доступ) — повторный скан позже")

        for adapter in PLAYER_ORDER[1:]:
            if episodes and all(item.best_resolution >= RESOLUTION_CEILING
                                for item in episodes):
                break
            if adapter.series_scope:
                await self._apply_vibix(episodes, http)
            else:
                await self._apply_adapter(episodes, adapter, http)

        facts: list[dict] = []
        for item in episodes:
            if not item.candidates:
                logger.warning("Skipping episode %s: no playable player (%s)",
                               item.page_url, item.reason or "нет источника")
                continue
            resolution, source, url = max(
                item.candidates,
                key=lambda candidate: (candidate[0], -_PRIORITY[candidate[1]]))
            item.video_url, item.resolution, item.source = url, resolution, source
            facts.append({
                "title": item.title,
                "url": item.video_url,
                "publication_date": item.publication_date,
                "resolution": item.resolution,
            })

        facts.sort(key=lambda fact: fact["publication_date"])
        report = self._availability_report(episodes)
        logger.info("Ruchime scan %s: серий %d, источники %s",
                    slug, len(facts), report["sources"])
        return {"videos": facts, "players": report}

    async def resolve_stream(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Resolve AllVideo embeds and Vibix episode references to a stream."""
        if any(domain in url for domain in ("fsst.online", "incvideo1.online")):
            return await self._resolve_allvideo(url, http)
        if self.match_url(url) and _EPISODE_IN_URL.search(url):
            return await self._resolve_vibix(url, http)
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
            parsed = urlparse(url)
            origin = (f"{parsed.scheme}://{parsed.netloc}/"
                      if parsed.scheme and parsed.netloc else "https://incvideo1.online/")
            return {"stream_url": stream_url, "headers": {"Referer": origin}}
        except Exception as exc:  # noqa: BLE001 - resolution is retried by download
            logger.warning("Failed resolving AllVideo stream for %s: %s", url, exc)
            return None

    async def _resolve_vibix(self, url: str, http: httpx.AsyncClient) -> dict | None:
        match = _EPISODE_IN_URL.search(url)
        if not match:
            return None
        number = int(match.group(1))
        try:
            playlist, key = await self._vibix_playlist(url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", url, exc)
            return None
        episode = next((item for item in playlist if item.number == number), None)
        if episode is None or not episode.streams:
            logger.warning("Episode %s is missing from the Vibix playlist", number)
            return None
        stream = max(episode.streams, key=lambda item: item.quality)
        try:
            response = await http.get(stream.url, follow_redirects=True, timeout=15.0)
            if response.status_code != 200:
                logger.warning("Vibix manifest HTTP %s for %s",
                               response.status_code, stream.url)
                return None
            text = parse_manifest(response.content, key)
        except (httpx.HTTPError, VibixProtocolError) as exc:
            logger.warning("Failed resolving Vibix manifest for %s: %s", url, exc)
            return None
        manifest = self._write_manifest(text, stream.url, self._extract_slug(url),
                                        number, stream.quality)
        return {"stream_url": f"file://{manifest}",
                "headers": {"Referer": "https://1.ruchime.org/"}}

    def _write_manifest(self, text: str, base_url: str, slug: str, number: int,
                        quality: int) -> Path:
        """Decode segment references to absolute URLs so ffmpeg reads a local file."""
        directory = (self._manifest_dir or Path("/tmp")) / "vibix"
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


def _merge_reports(first: dict, second: dict) -> dict:
    """Merge two preview reports, keeping the best resolution per player."""
    merged: dict[str, dict] = {}
    for name in list(first) + [key for key in second if key not in first]:
        left = first.get(name) or {}
        right = second.get(name) or {}
        resolutions = [value for value in (left.get("resolution"),
                                           right.get("resolution"))
                       if value is not None]
        available = None
        if left.get("available") is not None or right.get("available") is not None:
            available = bool(left.get("available") or right.get("available"))
        merged[name] = {
            "present": bool(left.get("present") or right.get("present")),
            "available": available,
            "resolution": max(resolutions) if resolutions else None,
            "reason": left.get("reason") or right.get("reason") or "",
        }
    return merged


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
