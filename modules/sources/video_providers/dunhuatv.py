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
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from core.reasons import describe, reason

from . import views
from ..players import cdnvideohub, kodik
from ..players.cdnvideohub import CdnVideoHubError, CdnVideoHubPlayer
from ..players.contract import Kind, PlayerRef
from ..players.http import USER_AGENT, get_answer
from ..players.kodik import KodikError, KodikPlayer
from ..players.vk_files import pick_file
from ..policy import Pacer, ask, probe_from, settle
from .base import BaseVideoProvider, StreamUnavailable
from .ranges import episode_ranges
from .table import ReleaseTable, Row

logger = logging.getLogger("sources.video.dunhuatv")

SITE = "https://dunhuatv.ru"
SITE_NAME = "dunhuatv.ru"
PAGE_TIMEOUT = 20.0
MAX_CONCURRENT = 6
# The site publishes no per-episode dates; a fixed one keeps candidate ids stable across scans.
DEFAULT_DATE = "1970-01-01T12:00:00Z"
# Player order as the user sees it: player 1 is CDN VideoHub (VK videos), player 2 is Kodik.
PLAYERS = ("cvh", "kodik")
_PRIORITY = {name: index for index, name in enumerate(PLAYERS)}
# Addresses written while the CDN VideoHub player was called "vk" keep working.
_LEGACY_PLAYER = {"vk": "cvh"}

_TITLE_PATH = re.compile(r"^/(\d+)-[^/]+\.html$")
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_ORIGINAL_NAME = re.compile(r"Как называется в оригинале\?</summary>\s*<div[^>]*>([^<]*)",
                            re.IGNORECASE)


@dataclass(frozen=True)
class TitlePage:
    name_ru: str
    name_en: str | None
    aggregator_id: int | None
    publisher_id: int
    kodik_frame: str | None


def _seasons_of(items: list) -> dict[int, set[int]]:
    """Seasons in which each episode number occurs anywhere in the playlist: the season is dropped for the
    whole catalog, so a number that two seasons share is ambiguous even between different voice-overs."""
    found: dict[int, set[int]] = {}
    for item in items:
        found.setdefault(item.episode, set()).add(item.season)
    return found


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


@dataclass
class _Catalog:
    sources: list[Source] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    transient: bool = False


def parse_title_page(text: str, fallback_name: str = "") -> TitlePage:
    heading = _H1.search(text)
    name = html.unescape(_TAGS.sub("", heading.group(1))).strip() if heading else ""
    original = _ORIGINAL_NAME.search(text)
    name_en = None
    if original:
        name_en = re.split(r"\s*\(", html.unescape(original.group(1)), maxsplit=1)[0].strip(" .") or None
    title_id, publisher = cdnvideohub.widget_of(text)
    frame = KodikPlayer().find(text, "")
    return TitlePage(name_ru=name or fallback_name, name_en=name_en, aggregator_id=title_id, publisher_id=publisher,
                     kodik_frame=frame.url if frame else None)


def no_players(causes) -> str:
    return reason("no_players", site=SITE_NAME, causes=list(causes)) if causes else reason("no_players_empty", site=SITE_NAME)


def split_address(url: str) -> tuple[str, Choice | None]:
    """Title page address without the query, and the choice the query carries."""
    parsed = urlparse(url)
    if not _TITLE_PATH.match(parsed.path):
        raise ValueError(reason("not_a_title_page", site=SITE_NAME, url=url))
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


def episode_ref(page_url: str, source: Source, number: int) -> PlayerRef:
    """The player reference of one episode of a source; its address is the candidate link."""
    if source.choice.player == "cvh":
        return CdnVideoHubPlayer.ref_of(page_url, source.episodes[number])
    assert source.serial is not None
    return KodikPlayer.ref_of(page_url, *source.serial, number)


class DunhuaTvProvider(BaseVideoProvider):
    name = "dunhuatv"
    domains = ("dunhuatv.ru",)
    has_voice_catalog = True

    def __init__(self, measure: cdnvideohub.Measure | None = None) -> None:
        self._cvh, self._kodik = CdnVideoHubPlayer(measure), KodikPlayer()
        self._players = {self._cvh.name: self._cvh, self._kodik.name: self._kodik}
        self._pacer = Pacer(0.0)

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
            raise ValueError(no_players(catalog.reasons.values()))
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
            raise ValueError(reason("players_silent", site=SITE_NAME, causes=list(catalog.reasons.values())))
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

        player = chosen.choice.player
        adapter = self._players[player]
        rows = [Row(number, page_url, self._episode_title(name, number), DEFAULT_DATE,
                    refs={player: episode_ref(page_url, chosen, number)}) for number in numbers]
        await ReleaseTable(page_url, rows).fill(adapter, http, self._pacer, gate=gate, on_asked=tick)
        found, skipped = views.facts(rows, frozenset(), _PRIORITY)
        unread = [row for row in skipped if row.unverified()]
        if unread and not found:
            raise ValueError(reason("episodes_unread", site=SITE_NAME, count=len(unread)))
        report = views.availability_report(rows, [adapter], frozenset(), _PRIORITY)
        report["source"] = {"player": player, "studio": chosen.choice.studio, "kind": chosen.choice.kind}
        if progress is not None:
            progress(total, total, "done")
        logger.info("Dunhuatv scan %s (%s): серий %d из %d", page_url, player, len(found), total)
        return {"videos": found, "players": report}

    async def resolve_stream(self, url: str, http: httpx.AsyncClient,
                             max_height: int | None = None) -> dict | None:
        """Fresh stream of a candidate link; None when the link is not ours."""
        player = self._player_of_link(url)
        if player is None:
            return None
        adapter = self._players[player]
        if (cdnvideohub.vk_id_of(url) if player == "cvh" else kodik.parse_link(url)) is None:
            raise StreamUnavailable(reason("player_failed", player=adapter.label,
                                           cause=reason("bad_link", link=urlparse(url).fragment)))
        asked = adapter.capped(max_height) if player == "cvh" else adapter
        ref = PlayerRef(player, url)
        verdict = await settle(asked, ref, http, SITE + "/", pacer=self._pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None:
            probe = probe_from(verdict, ref, adapter.label)
            raise StreamUnavailable(reason("player_failed", player=adapter.label, cause=probe.reason), probe.transient)
        headers = dict(cdnvideohub.STREAM_HEADERS) if player == "cvh" else {"Referer": kodik.ORIGIN + "/"}
        return {"stream_url": picked[1], "headers": headers}

    async def _read_page(self, url: str, http: httpx.AsyncClient) -> str:
        answer = await ask(lambda: get_answer(http, url, "", headers={"User-Agent": USER_AGENT}, timeout=PAGE_TIMEOUT))
        if answer.status is None:
            raise ValueError(reason("site_unreachable", site=SITE_NAME, error=answer.error))
        if answer.status != 200:
            raise ValueError(reason("site_status", site=SITE_NAME, status=answer.status, url=url))
        return answer.text

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
            catalog.reasons["cvh"] = reason("page_no_player", player="CVH")
            return
        answer = await ask(lambda: cdnvideohub.playlist_request(http, page.aggregator_id, page.publisher_id))
        try:
            items = cdnvideohub.read_playlist(answer)
        except CdnVideoHubError as exc:
            catalog.reasons["cvh"], catalog.transient = exc.reason, catalog.transient or exc.transient
            return
        clashing = sorted(episode for episode, found in _seasons_of(items).items() if len(found) > 1)
        if clashing:
            catalog.reasons["cvh"] = reason("season_clash", player="CVH", episodes=", ".join(map(str, clashing)))
            return
        grouped: dict[tuple[str, str], dict[int, str]] = {}
        for item in items:
            grouped.setdefault((item.studio, item.voice_type), {})[item.episode] = item.vk_id
        catalog.sources += [Source(Choice("cvh", studio or "без студии", kind), episodes)
                            for (studio, kind), episodes in grouped.items()]
        if not grouped:
            catalog.reasons["cvh"] = reason("title_no_videos", player="CVH")

    async def _add_kodik(self, catalog: _Catalog, page: TitlePage, http: httpx.AsyncClient,
                         wanted: Choice | None) -> None:
        if not page.kodik_frame:
            catalog.reasons["kodik"] = reason("page_no_player", player="Kodik")
            return
        try:
            default = await self._open_kodik(http, page.kodik_frame)
        except KodikError as exc:
            catalog.reasons["kodik"], catalog.transient = exc.reason, catalog.transient or exc.transient
            return
        for voice in default.voices:
            choice = Choice("kodik", voice.studio, voice.kind)
            if wanted is not None and choice != wanted:
                continue
            try:
                session = (default if kodik.voice_iframe(voice) == page.kodik_frame
                           else await self._open_kodik(http, kodik.voice_iframe(voice)))
            except KodikError as exc:
                logger.warning("Dunhuatv: озвучка Kodik %s не открылась: %s", voice.studio, describe(exc.reason))
                catalog.reasons["kodik"] = reason("voice_failed", studio=voice.studio, cause=exc.reason)
                catalog.transient = catalog.transient or exc.transient
                continue
            self._kodik.remember(http, session, kodik.voice_iframe(voice))
            catalog.sources.append(Source(
                choice, {number: "" for number in session.episodes}, (voice.media_id, voice.media_hash)))
        if not any(s.choice.player == "kodik" for s in catalog.sources):
            catalog.reasons["kodik"] = reason("title_no_voices", player="Kodik")

    async def _open_kodik(self, http: httpx.AsyncClient, url: str) -> kodik.KodikSession:
        """An iframe opened under the one policy; the way the embedding site loads it signs the page."""
        url = kodik.normalize_iframe(url)
        answer = await ask(lambda: self._kodik.open_page(http, url, SITE + "/"))
        return kodik.read_session(url, answer)

    @staticmethod
    def _choose(sources: list[Source], wanted: Choice | None) -> Source:
        """The wanted source, or the first one (player 1 before player 2) when none is named."""
        if wanted is None:
            if not sources:
                raise ValueError(no_players([]))
            return min(sources, key=lambda s: PLAYERS.index(s.choice.player))
        for source in sources:
            if source.choice == wanted:
                return source
        raise ValueError(reason("voice_not_found", site=SITE_NAME, studio=wanted.studio, player=wanted.player))

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
            "links": {str(number): episode_ref(page_url, source, number).url for number in numbers},
        }


    def _player_of_link(self, url: str) -> str | None:
        """The player a candidate link of this site belongs to."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host not in self.domains:
            return None
        if parsed.fragment.startswith(("cvh=", "vk=")):
            return "cvh"
        return "kodik" if parsed.fragment.startswith("kodik=") else None

    @staticmethod
    def _episode_title(name: str, number: int) -> str:
        return f"{name} - {number} серия"

    @staticmethod
    def _quick_report(catalog: _Catalog, player: str) -> dict:
        present = any(source.choice.player == player for source in catalog.sources)
        return {"present": present, "available": present, "resolution": None,
                "reason": reason("checked_on_scan") if present else catalog.reasons.get(player, reason("no_data"))}
