"""Video provider for dunhuatv.ru: one title page, two players.

VK (CDN VideoHub) carries the high resolutions and several voice-overs; Kodik covers the
episodes VK does not have yet. A candidate link is a fragment of the title page, so the
identity of an episode stays stable and the stream is resolved right before the download.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import httpx

from . import cdnvideohub, kodik
from .base import BaseVideoProvider, StreamUnavailable
from .cdnvideohub import CdnVideoHubError, Rung, VoiceVideo
from .kodik import KodikError, KodikSession
from .retry import send_with_retry

logger = logging.getLogger("sources.video.dunhuatv")

SITE = "https://dunhuatv.ru"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
PAGE_TIMEOUT = 20.0
MAX_CONCURRENT = 6
# Kodik never offers more than 720p, so a VK video at that height makes a Kodik probe pointless.
KODIK_PROBE_BELOW = 720
# The site publishes no per-episode dates; a fixed one keeps candidate ids stable across scans.
DEFAULT_DATE = "1970-01-01T12:00:00Z"
DEFAULT_PUBLISHER = 1110

_TITLE_PATH = re.compile(r"^/(\d+)-[^/]+\.html$")
_PLAYER_WIDGET = re.compile(r"<video-player\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_KODIK_FRAME = re.compile(r"""<iframe\b[^>]*\bdata-src=["']([^"']*kodikplayer\.com[^"']*)["']""",
                          re.IGNORECASE)
_ORIGINAL_NAME = re.compile(r"Как называется в оригинале\?</summary>\s*<div[^>]*>([^<]*)",
                            re.IGNORECASE)
_KODIK_SERIAL = re.compile(r"/serial/(\d+)/([a-f0-9]+)/")
_LINK = re.compile(r"^(vk|kodik)=(.+)$")


@dataclass(frozen=True)
class TitlePage:
    name_ru: str
    name_en: str | None
    aggregator_id: int | None
    publisher_id: int
    kodik_frame: str | None


@dataclass(frozen=True)
class _Pick:
    """The best offer of one player for one episode."""

    source: str
    resolution: int
    link: str


@dataclass
class _Probes:
    """What one player yielded over a set of episodes."""

    picks: dict[int, _Pick] = field(default_factory=dict)
    failures: dict[int, str] = field(default_factory=dict)
    transient: int = 0


def parse_title_page(text: str, fallback_name: str = "") -> TitlePage:
    heading = _H1.search(text)
    name = html.unescape(_TAGS.sub("", heading.group(1))).strip() if heading else ""
    original = _ORIGINAL_NAME.search(text)
    name_en = None
    if original:
        name_en = re.split(r"\s*\(", html.unescape(original.group(1)), maxsplit=1)[0].strip(" .") or None
    found = _PLAYER_WIDGET.search(text)
    widget = dict(_ATTR.findall(found.group(1))) if found else {}
    title_id = widget.get("data-title-id", "")
    publisher = widget.get("data-publisher-id", "")
    frame = _KODIK_FRAME.search(text)
    return TitlePage(
        name_ru=name or fallback_name, name_en=name_en,
        aggregator_id=int(title_id) if title_id.isdigit() else None,
        publisher_id=int(publisher) if publisher.isdigit() else DEFAULT_PUBLISHER,
        kodik_frame=kodik.normalize_iframe(html.unescape(frame.group(1))) if frame else None)


def vk_link(page_url: str, vk_id: str) -> str:
    return f"{page_url}#vk={vk_id}"


def kodik_link(page_url: str, media_id: str, media_hash: str, episode: int) -> str:
    return f"{page_url}#kodik={media_id}/{media_hash}/{episode}"


class DunhuaTvProvider(BaseVideoProvider):
    name = "dunhuatv"
    domains = ("dunhuatv.ru",)

    def __init__(self, measure: cdnvideohub.Measure | None = None) -> None:
        self._measure = measure or cdnvideohub.measure_rung
        self._heights: dict[tuple[str, str], int | None] = {}

    def match_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return host in self.domains and _TITLE_PATH.match(parsed.path) is not None

    def canonical_url(self, url: str) -> str:
        path = urlparse(url).path
        if not _TITLE_PATH.match(path):
            raise ValueError(f"Не страница тайтла dunhuatv.ru: {url}")
        return f"{SITE}{path}"

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Title page, the VK playlist and the Kodik episode list: no per-episode probing."""
        page_url = self.canonical_url(url)
        page = parse_title_page(await self._read_page(page_url, http), page_url)
        voices, vk_reason, _ = await self._playlist(page, http)
        session, kodik_reason, _ = await self._kodik_session(page, http)

        numbers = sorted({item.episode for item in voices}
                         | (set(session.episodes) if session else set()))
        titles = [self._episode_title(page.name_ru, number) for number in numbers]
        return {
            "canonical_url": page_url,
            "title": {"ru": page.name_ru, "en": page.name_en},
            "titles": titles,
            "episodes": [{"number": number, "title": title, "date": None}
                         for number, title in zip(numbers, titles)],
            # Quality is a per-episode scan fact, not a page preview.
            "resolution": None,
            "videos": [{"title": title, "resolution": None} for title in titles],
            "episodes_count": len(titles),
            "provider": self.name,
            "source_type": "web_video",
            "releases": [],
            "players": {"vk": self._quick_report(bool(voices), vk_reason),
                        "kodik": self._quick_report(session is not None, kodik_reason)},
        }

    async def scan_episodes(self, url: str, http: httpx.AsyncClient, series: dict,
                            episodes: list[int] | None = None,
                            progress: Callable[[int, int, str], None] | None = None) -> dict:
        page_url = self.canonical_url(url)
        page = parse_title_page(await self._read_page(page_url, http), page_url)
        name = series.get("name") or page.name_ru
        voices, vk_reason, vk_transient = await self._playlist(page, http)
        session, kodik_reason, kodik_transient = await self._kodik_session(page, http)
        if vk_transient or kodik_transient:
            raise ValueError("dunhuatv.ru: плеер не ответил "
                             f"({vk_reason or kodik_reason}) — повторный скан позже")

        wanted = set(episodes) if episodes is not None else None
        items = [item for item in voices if wanted is None or item.episode in wanted]
        kodik_numbers = {number for number in (session.episodes if session else {})
                         if wanted is None or number in wanted}
        done, total = 0, len(items) + len(kodik_numbers)
        gate = asyncio.Semaphore(MAX_CONCURRENT)

        def tick() -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total, "players")

        vk = await self._probe_vk(items, page_url, http, gate, tick)
        fallback = sorted(number for number in kodik_numbers if number not in vk.picks
                          or vk.picks[number].resolution < KODIK_PROBE_BELOW)
        ko = (await self._probe_kodik(session, fallback, page_url, http, gate, tick)
              if session and fallback else _Probes())
        if vk.transient or ko.transient:
            raise ValueError(f"dunhuatv.ru: не прочитаны {vk.transient + ko.transient} серий "
                             "(плеер ограничил доступ) — повторный скан позже")

        facts, selected, holes = [], Counter(), []
        numbers = sorted({item.episode for item in items} | kodik_numbers)
        for number in numbers:
            offers = [pick for pick in (vk.picks.get(number), ko.picks.get(number)) if pick]
            if not offers:
                holes.append({"episode": number, "unverified": False,
                              "reason": vk.failures.get(number) or ko.failures.get(number)
                              or "нет доступного плеера"})
                continue
            selected[max(offers, key=lambda pick: pick.resolution).source] += 1
            facts.extend({"title": self._episode_title(name, number), "url": pick.link,
                          "download_url": None, "publication_date": DEFAULT_DATE,
                          "resolution": pick.resolution} for pick in offers)
        if progress is not None:
            progress(total, total, "done")
        logger.info("Dunhuatv scan %s: серий %d, источники %s", page_url, len(numbers),
                    dict(selected))
        return {"videos": facts, "players": {
            "episodes": len(numbers),
            "sources": dict(selected),
            "players": {
                "vk": self._player_report(vk, selected["vk"],
                                          len({item.episode for item in items}), vk_reason),
                "kodik": self._player_report(ko, selected["kodik"], len(kodik_numbers),
                                             kodik_reason),
            },
            "holes": holes,
        }}

    async def resolve_stream(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Fresh stream of a candidate link; None when the link is not ours."""
        target = self._parse_link(url)
        if target is None:
            return None
        kind, value = target
        if kind == "vk":
            return await self._resolve_vk(value, http)
        return await self._resolve_kodik(value, http)

    # --- page and playlists -------------------------------------------------------------

    async def _read_page(self, url: str, http: httpx.AsyncClient) -> str:
        try:
            response = await send_with_retry(lambda: http.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=PAGE_TIMEOUT,
                follow_redirects=True))
        except httpx.HTTPError as exc:
            raise ValueError(f"dunhuatv.ru недоступен: {exc}") from exc
        if response.status_code != 200:
            raise ValueError(f"dunhuatv.ru: страница вернула {response.status_code}: {url}")
        return response.text

    async def _playlist(self, page: TitlePage, http: httpx.AsyncClient,
                        ) -> tuple[list[VoiceVideo], str, bool]:
        """(items, reason it is empty, whether the failure is worth a later retry)."""
        if page.aggregator_id is None:
            return [], "на странице нет плеера VK", False
        try:
            items = await cdnvideohub.fetch_playlist(http, page.aggregator_id, page.publisher_id)
        except CdnVideoHubError as exc:
            return [], exc.reason, exc.transient
        return items, "" if items else "у тайтла нет VK-видео", False

    async def _kodik_session(self, page: TitlePage, http: httpx.AsyncClient,
                             ) -> tuple[KodikSession | None, str, bool]:
        if not page.kodik_frame:
            return None, "на странице нет плеера Kodik", False
        try:
            return await kodik.open_session(http, page.kodik_frame, SITE + "/"), "", False
        except KodikError as exc:
            return None, exc.reason, exc.transient

    # --- probes -------------------------------------------------------------------------

    async def _probe_vk(self, items: list[VoiceVideo], page_url: str, http: httpx.AsyncClient,
                        gate: asyncio.Semaphore, tick: Callable[[], None]) -> _Probes:
        async def probe(item: VoiceVideo) -> Rung | CdnVideoHubError:
            async with gate:
                try:
                    return await cdnvideohub.best_rung(
                        await cdnvideohub.fetch_rungs(http, item.vk_id),
                        self._cached_measure(item.vk_id))
                except CdnVideoHubError as exc:
                    return exc
                finally:
                    tick()

        result = _Probes()
        for item, outcome in zip(items, await asyncio.gather(*(probe(i) for i in items))):
            if isinstance(outcome, CdnVideoHubError):
                result.transient += outcome.transient
                result.failures.setdefault(
                    item.episode, f"VK ({item.studio or 'без студии'}): {outcome.reason}")
                continue
            known = result.picks.get(item.episode)
            # Equal resolutions: the voice-over the aggregator lists first keeps the episode.
            if known is None or outcome.height > known.resolution:
                result.picks[item.episode] = _Pick("vk", outcome.height,
                                                   vk_link(page_url, item.vk_id))
        return result

    async def _probe_kodik(self, session: KodikSession, numbers: list[int], page_url: str,
                           http: httpx.AsyncClient, gate: asyncio.Semaphore,
                           tick: Callable[[], None]) -> _Probes:
        served = _KODIK_SERIAL.search(session.iframe_url)
        if served is None:
            return _Probes()
        media_id, media_hash = served.groups()

        async def probe(number: int) -> dict[int, str] | KodikError:
            async with gate:
                try:
                    return await kodik.episode_qualities(http, session, number)
                except KodikError as exc:
                    return exc
                finally:
                    tick()

        result = _Probes()
        for number, outcome in zip(numbers, await asyncio.gather(*(probe(n) for n in numbers))):
            if isinstance(outcome, KodikError):
                result.transient += outcome.transient
                result.failures[number] = f"Kodik: {outcome.reason}"
                continue
            result.picks[number] = _Pick("kodik", max(outcome),
                                         kodik_link(page_url, media_id, media_hash, number))
        return result

    def _cached_measure(self, vk_id: str) -> cdnvideohub.Measure:
        async def measure(rung: Rung) -> int | None:
            key = (vk_id, rung.key)
            if key not in self._heights:
                self._heights[key] = await self._measure(rung)
            return self._heights[key]
        return measure

    # --- stream resolution --------------------------------------------------------------

    async def _resolve_vk(self, vk_id: str, http: httpx.AsyncClient) -> dict:
        try:
            rung = await cdnvideohub.best_rung(await cdnvideohub.fetch_rungs(http, vk_id),
                                               self._cached_measure(vk_id))
        except CdnVideoHubError as exc:
            raise StreamUnavailable(f"VK: {exc.reason}", exc.transient) from exc
        return {"stream_url": rung.url, "headers": dict(cdnvideohub.STREAM_HEADERS)}

    async def _resolve_kodik(self, value: str, http: httpx.AsyncClient) -> dict:
        parts = value.split("/")
        if len(parts) != 3 or not parts[2].isdigit():
            raise StreamUnavailable(f"Kodik: непонятная ссылка {value!r}")
        media_id, media_hash, episode = parts[0], parts[1], int(parts[2])
        try:
            session = await kodik.open_session(
                http, kodik.iframe_url(media_id, media_hash), SITE + "/")
            qualities = await kodik.episode_qualities(http, session, episode)
        except KodikError as exc:
            raise StreamUnavailable(f"Kodik: {exc.reason}", exc.transient) from exc
        return {"stream_url": qualities[max(qualities)],
                "headers": {"Referer": kodik.ORIGIN + "/"}}

    # --- reports and names --------------------------------------------------------------

    def _parse_link(self, url: str) -> tuple[str, str] | None:
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        found = _LINK.match(parsed.fragment)
        if host not in self.domains or found is None:
            return None
        return found.group(1), found.group(2)

    @staticmethod
    def _episode_title(name: str, number: int) -> str:
        return f"{name} - {number} серия"

    @staticmethod
    def _quick_report(present: bool, reason: str) -> dict:
        return {"present": present, "available": present, "resolution": None,
                "reason": "проверяется при сканировании" if present else reason}

    @staticmethod
    def _player_report(probes: _Probes, selected: int, present: int, reason: str) -> dict:
        reasons = Counter(probes.failures.values())
        return {
            "present": present,
            "available": len(probes.picks),
            "selected": selected,
            "unverified": 0,
            "max_resolution": max((pick.resolution for pick in probes.picks.values()),
                                  default=None),
            "reason": reasons.most_common(1)[0][0] if reasons else ("" if present else reason),
        }
