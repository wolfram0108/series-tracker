"""Video provider for dunhuatv.ru: one title page, two players, several voice-overs.

A series takes its episodes from ONE player and ONE voice-over; the choice travels in the
series address (`?player=cvh&voice=<studio>&kind=<type>`). A candidate link is a fragment of
the title page, so the identity of an episode stays stable and the stream is resolved right
before the download.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import cdnvideohub, kodik
from .base import BaseVideoProvider, StreamUnavailable
from .cdnvideohub import CdnVideoHubError, Rung
from .kodik import KodikError, KodikSession
from .retry import send_with_retry

logger = logging.getLogger("sources.video.dunhuatv")

SITE = "https://dunhuatv.ru"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
PAGE_TIMEOUT = 20.0
MAX_CONCURRENT = 6
# The site publishes no per-episode dates; a fixed one keeps candidate ids stable across scans.
DEFAULT_DATE = "1970-01-01T12:00:00Z"
DEFAULT_PUBLISHER = 1110
# Player order as the user sees it: player 1 is CDN VideoHub (VK videos), player 2 is Kodik.
PLAYERS = ("cvh", "kodik")
# Addresses written while the CDN VideoHub player was called "vk" keep working.
_LEGACY_PLAYER = {"vk": "cvh"}

_TITLE_PATH = re.compile(r"^/(\d+)-[^/]+\.html$")
_PLAYER_WIDGET = re.compile(r"<video-player\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_KODIK_FRAME = re.compile(r"""<iframe\b[^>]*\bdata-src=["']([^"']*kodikplayer\.com[^"']*)["']""",
                          re.IGNORECASE)
_ORIGINAL_NAME = re.compile(r"Как называется в оригинале\?</summary>\s*<div[^>]*>([^<]*)",
                            re.IGNORECASE)
_LINK = re.compile(r"^(cvh|vk|kodik)=(.+)$")


@dataclass(frozen=True)
class TitlePage:
    name_ru: str
    name_en: str | None
    aggregator_id: int | None
    publisher_id: int
    kodik_frame: str | None


@dataclass(frozen=True)
class Choice:
    """A player and a voice-over: what a series downloads."""

    player: str
    studio: str
    kind: str = ""


@dataclass
class Source:
    """One selectable (player, voice-over) with the episodes it really has."""

    choice: Choice
    episodes: dict[int, str]
    serial: tuple[str, str] | None = None
    session: KodikSession | None = None


@dataclass
class _Catalog:
    sources: list[Source] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    transient: bool = False


@dataclass
class _Probes:
    resolutions: dict[int, tuple[int, str]] = field(default_factory=dict)
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


def split_address(url: str) -> tuple[str, Choice | None]:
    """Title page address without the query, and the choice the query carries."""
    parsed = urlparse(url)
    if not _TITLE_PATH.match(parsed.path):
        raise ValueError(f"Не страница тайтла dunhuatv.ru: {url}")
    query = parse_qs(parsed.query)
    player = (query.get("player") or [""])[0]
    player = _LEGACY_PLAYER.get(player, player)
    studio = (query.get("voice") or [""])[0]
    choice = (Choice(player, studio, (query.get("kind") or [""])[0])
              if player in PLAYERS and studio else None)
    return f"{SITE}{parsed.path}", choice


def address_of(page_url: str, choice: Choice) -> str:
    query = {"player": choice.player, "voice": choice.studio}
    if choice.kind:
        query["kind"] = choice.kind
    return f"{page_url}?{urlencode(query)}"


def cvh_link(page_url: str, vk_id: str) -> str:
    return f"{page_url}#cvh={vk_id}"


def kodik_link(page_url: str, media_id: str, media_hash: str, episode: int) -> str:
    return f"{page_url}#kodik={media_id}/{media_hash}/{episode}"


def episode_ranges(numbers) -> str:
    """'1-471, 473-501': the real coverage of a voice-over, holes included."""
    ordered = sorted(set(numbers))
    spans: list[list[int]] = []
    for number in ordered:
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


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
        page_url, choice = split_address(url)
        return address_of(page_url, choice) if choice else page_url

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Title page and every selectable voice-over; titles are those of the chosen one."""
        page_url, wanted = split_address(url)
        page = parse_title_page(await self._read_page(page_url, http), page_url)
        catalog = await self._catalog(page, http, wanted, everything=True)
        if not catalog.sources:
            raise ValueError("dunhuatv.ru: у тайтла нет доступных плееров "
                             f"({'; '.join(catalog.reasons.values()) or 'пусто'})")
        chosen = self._choose(catalog.sources, wanted)
        numbers = sorted(chosen.episodes)
        titles = [self._episode_title(page.name_ru, number) for number in numbers]
        return {
            "canonical_url": address_of(page_url, chosen.choice),
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
            "players": {player: self._quick_report(catalog, player) for player in PLAYERS},
            "sources": [self._describe(page_url, source) for source in catalog.sources],
            "selected_source": address_of(page_url, chosen.choice),
        }

    async def scan_episodes(self, url: str, http: httpx.AsyncClient, series: dict,
                            episodes: list[int] | None = None,
                            progress: Callable[[int, int, str], None] | None = None) -> dict:
        page_url, wanted = split_address(url)
        page = parse_title_page(await self._read_page(page_url, http), page_url)
        catalog = await self._catalog(page, http, wanted, everything=False)
        if catalog.transient and not catalog.sources:
            raise ValueError("dunhuatv.ru: плеер не ответил "
                             f"({'; '.join(catalog.reasons.values())}) — повторный скан позже")
        chosen = self._choose(catalog.sources, wanted)
        name = series.get("name") or page.name_ru
        numbers = [n for n in sorted(chosen.episodes) if episodes is None or n in set(episodes)]
        done, total = 0, len(numbers)
        gate = asyncio.Semaphore(MAX_CONCURRENT)

        def tick() -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total, "players")

        probes = await self._probe(chosen, numbers, page_url, http, gate, tick)
        if probes.transient:
            raise ValueError(f"dunhuatv.ru: не прочитаны {probes.transient} серий "
                             "(плеер ограничил доступ) — повторный скан позже")
        facts = [{"title": self._episode_title(name, number), "url": link,
                  "download_url": None, "publication_date": DEFAULT_DATE,
                  "resolution": resolution}
                 for number, (resolution, link) in sorted(probes.resolutions.items())]
        holes = [{"episode": number, "unverified": False,
                  "reason": probes.failures.get(number) or "нет доступного плеера"}
                 for number in numbers if number not in probes.resolutions]
        if progress is not None:
            progress(total, total, "done")
        player = chosen.choice.player
        logger.info("Dunhuatv scan %s (%s): серий %d из %d", page_url, player, len(facts), total)
        return {"videos": facts, "players": {
            "episodes": len(facts),
            "sources": {player: len(facts)},
            "players": {player: {
                "present": total, "available": len(facts), "selected": len(facts),
                "unverified": 0,
                "max_resolution": max((r for r, _ in probes.resolutions.values()), default=None),
                "reason": (Counter(probes.failures.values()).most_common(1)[0][0]
                           if probes.failures else ""),
            }},
            "source": {"player": player, "studio": chosen.choice.studio,
                       "kind": chosen.choice.kind},
            "holes": holes,
        }}

    async def resolve_stream(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Fresh stream of a candidate link; None when the link is not ours."""
        target = self._parse_link(url)
        if target is None:
            return None
        kind, value = target
        if kind in ("cvh", "vk"):
            return await self._resolve_cvh(value, http)
        return await self._resolve_kodik(value, http)

    # --- page and catalog ---------------------------------------------------------------

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

    async def _catalog(self, page: TitlePage, http: httpx.AsyncClient, wanted: Choice | None,
                       everything: bool) -> _Catalog:
        """The sources worth loading: all of them for the choice list, else just the wanted one."""
        catalog = _Catalog()
        if everything or wanted is None or wanted.player == "cvh":
            await self._add_cvh(catalog, page, http)
        needs_kodik = everything or (wanted.player == "kodik" if wanted else not catalog.sources)
        if needs_kodik:
            await self._add_kodik(catalog, page, http, None if everything else wanted)
        return catalog

    async def _add_cvh(self, catalog: _Catalog, page: TitlePage, http: httpx.AsyncClient) -> None:
        if page.aggregator_id is None:
            catalog.reasons["cvh"] = "на странице нет плеера CVH"
            return
        try:
            items = await cdnvideohub.fetch_playlist(http, page.aggregator_id, page.publisher_id)
        except CdnVideoHubError as exc:
            catalog.reasons["cvh"], catalog.transient = exc.reason, catalog.transient or exc.transient
            return
        grouped: dict[tuple[str, str], dict[int, str]] = {}
        for item in items:
            grouped.setdefault((item.studio, item.voice_type), {})[item.episode] = item.vk_id
        catalog.sources += [Source(Choice("cvh", studio or "без студии", kind), episodes)
                            for (studio, kind), episodes in grouped.items()]
        if not grouped:
            catalog.reasons["cvh"] = "у тайтла нет видео CVH"

    async def _add_kodik(self, catalog: _Catalog, page: TitlePage, http: httpx.AsyncClient,
                         wanted: Choice | None) -> None:
        if not page.kodik_frame:
            catalog.reasons["kodik"] = "на странице нет плеера Kodik"
            return
        try:
            default = await kodik.open_session(http, page.kodik_frame, SITE + "/")
        except KodikError as exc:
            catalog.reasons["kodik"], catalog.transient = exc.reason, catalog.transient or exc.transient
            return
        for voice in default.voices:
            choice = Choice("kodik", voice.studio, voice.kind)
            if wanted is not None and choice != wanted:
                continue
            try:
                session = (default if kodik.voice_iframe(voice) == page.kodik_frame
                           else await kodik.open_session(http, kodik.voice_iframe(voice), SITE + "/"))
            except KodikError as exc:
                logger.warning("Dunhuatv: озвучка Kodik %s не открылась: %s", voice.studio, exc.reason)
                catalog.reasons["kodik"] = f"{voice.studio}: {exc.reason}"
                catalog.transient = catalog.transient or exc.transient
                continue
            catalog.sources.append(Source(
                choice, {number: "" for number in session.episodes},
                (voice.media_id, voice.media_hash), session))
        if not any(s.choice.player == "kodik" for s in catalog.sources):
            catalog.reasons["kodik"] = "у тайтла нет озвучек Kodik"

    @staticmethod
    def _choose(sources: list[Source], wanted: Choice | None) -> Source:
        """The wanted source, or the first one (player 1 before player 2) when none is named."""
        if wanted is None:
            if not sources:
                raise ValueError("dunhuatv.ru: у тайтла нет доступных плееров")
            return min(sources, key=lambda s: PLAYERS.index(s.choice.player))
        for source in sources:
            if source.choice == wanted:
                return source
        raise ValueError(f"dunhuatv.ru: озвучка «{wanted.studio}» ({wanted.player}) "
                         "не найдена у тайтла — выберите источник заново")

    @staticmethod
    def _describe(page_url: str, source: Source) -> dict:
        numbers = sorted(source.episodes)
        return {
            "url": address_of(page_url, source.choice),
            "player": source.choice.player,
            "studio": source.choice.studio,
            "kind": source.choice.kind,
            "first": numbers[0] if numbers else None,
            "last": numbers[-1] if numbers else None,
            "count": len(numbers),
            "ranges": episode_ranges(numbers),
            "episodes": numbers,
        }

    # --- probes -------------------------------------------------------------------------

    async def _probe(self, source: Source, numbers: list[int], page_url: str,
                     http: httpx.AsyncClient, gate: asyncio.Semaphore,
                     tick: Callable[[], None]) -> _Probes:
        async def one(number: int) -> tuple[int, str] | CdnVideoHubError | KodikError:
            async with gate:
                try:
                    if source.choice.player == "cvh":
                        vk_id = source.episodes[number]
                        rung = await cdnvideohub.best_rung(
                            await cdnvideohub.fetch_rungs(http, vk_id), self._cached_measure(vk_id))
                        return rung.height, cvh_link(page_url, vk_id)
                    assert source.session is not None and source.serial is not None
                    qualities = await kodik.episode_qualities(http, source.session, number)
                    return max(qualities), kodik_link(page_url, *source.serial, number)
                except (CdnVideoHubError, KodikError) as exc:
                    return exc
                finally:
                    tick()

        label = "CVH" if source.choice.player == "cvh" else "Kodik"
        result = _Probes()
        for number, outcome in zip(numbers, await asyncio.gather(*(one(n) for n in numbers))):
            if isinstance(outcome, (CdnVideoHubError, KodikError)):
                result.transient += outcome.transient
                result.failures[number] = f"{label}: {outcome.reason}"
            else:
                result.resolutions[number] = outcome
        return result

    def _cached_measure(self, vk_id: str) -> cdnvideohub.Measure:
        async def measure(rung: Rung) -> int | None:
            key = (vk_id, rung.key)
            if key not in self._heights:
                self._heights[key] = await self._measure(rung)
            return self._heights[key]
        return measure

    # --- stream resolution --------------------------------------------------------------

    async def _resolve_cvh(self, vk_id: str, http: httpx.AsyncClient) -> dict:
        try:
            rung = await cdnvideohub.best_rung(await cdnvideohub.fetch_rungs(http, vk_id),
                                               self._cached_measure(vk_id))
        except CdnVideoHubError as exc:
            raise StreamUnavailable(f"CVH: {exc.reason}", exc.transient) from exc
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

    # --- names and reports --------------------------------------------------------------

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
    def _quick_report(catalog: _Catalog, player: str) -> dict:
        present = any(source.choice.player == player for source in catalog.sources)
        return {"present": present, "available": present, "resolution": None,
                "reason": ("проверяется при сканировании" if present
                           else catalog.reasons.get(player, "нет данных"))}
