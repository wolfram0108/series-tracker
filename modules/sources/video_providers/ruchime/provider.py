"""Video provider for Ruchime (https://1.ruchime.org).

Players are probed in the fixed priority order VK -> AllVideo -> Vibix. A player
without a playable stream never becomes a candidate; Vibix needs one browser
capture for the whole series and is resolved again right before download.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from core.reasons import describe, reason

from ..base import BaseVideoProvider, StreamUnavailable
from ...players.contract import Kind, Player, PlayerRef
from ...players.vibix import VibixFailure, VibixPlayer, link_of as vibix_link, pick_season
from ...players.vibix_protocol import Episode, VibixProtocolError, VibixUnavailable
from ...players.vk_files import pick_file
from ...policy import Pacer, probe_from, settle
from ..players import PLAYER_ORDER
from ..ranges import episode_ranges
from .. import views
from .address import Choice, address_of, choice_of
from .site import (MAX_CONCURRENT_PAGES, SITE_ROOT, Site, episode_title, extract_slug, parse_date, release_episodes,
                   release_names)
from ..table import Cell, ReleaseTable, Row, probe

logger = logging.getLogger("sources.video.ruchime")

PROBE_INTERVAL_S = 0.12
RESOLUTION_CEILING = 2160
_PRIORITY = {adapter.name: index for index, adapter in enumerate(PLAYER_ORDER)}
_PLAYERS = {adapter.name: adapter for adapter in PLAYER_ORDER}
# How long what the coverage has read stays good for a scan: the series is usually added right after it.
SURVEY_TTL_S = 900

_EPISODE_IN_URL = re.compile(r"/episode-(\d+)")


class RuchimeProvider(BaseVideoProvider):
    name = "ruchime"
    domains = ("1.ruchime.org", "ruchime.org")

    def __init__(self, browser_getter=None, key_path: str | Path | None = None,
                 manifest_dir: str | Path | None = None) -> None:
        self.vibix = VibixPlayer(browser_getter, key_path, manifest_dir)
        self._site = Site()
        # Releases read completely, for the scan that follows; one request at a time reads a release.
        self._tables: dict[str, ReleaseTable] = {}
        self._table_locks: dict[str, asyncio.Lock] = {}
        self._probe_pacer = Pacer(lambda: PROBE_INTERVAL_S)

    def configure(self, *, browser_getter=None, key_path: str | Path | None = None,
                  manifest_dir: str | Path | None = None) -> None:
        """Late wiring from the sources module (browser pool and cache paths)."""
        self.vibix.configure(browser_getter=browser_getter, key_path=key_path, manifest_dir=manifest_dir)

    def match_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            return host in self.domains and "/release/" in parsed.path
        except Exception:
            return False

    _extract_slug = staticmethod(extract_slug)

    def _page_url(self, url: str) -> str:
        return f"https://1.ruchime.org/release/{self._extract_slug(url)}"

    def canonical_url(self, url: str) -> str:
        return address_of(self._page_url(url), choice_of(url))

    _parse_date = staticmethod(parse_date)
    _episode_title = staticmethod(episode_title)

    async def _wait_probe_slot(self) -> None:
        """Spread player probes so a burst is not read as a missing source."""
        await self._probe_pacer.wait()

    @staticmethod
    def _needs_lower_source(rows: list[Row], adapter_name: str, off: frozenset[str] = frozenset()) -> list[Row]:
        """Check lower sources while a higher resolution is still possible."""
        return [row for row in rows if adapter_name in row.refs and row.best(off) < RESOLUTION_CEILING]

    async def _apply_vibix(self, table: ReleaseTable, rows: list[Row], http: httpx.AsyncClient, choice: Choice) -> None:
        """Map the episodes to the entries of the series catalog; the player is asked where Vibix may outrank the others."""
        off = choice.off
        targets = self._needs_lower_source(rows, "vibix", off)
        if not targets:
            return

        def refuse(row: Row, why: str) -> None:
            row.cells["vibix"] = Cell(False, reason=why)
            if not row.candidates(off):
                row.reason = why

        try:
            playlist, _key = await self.vibix.playlist(targets[0].page_url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix playlist unavailable for %s: %s", targets[0].page_url, exc)
            for row in targets:
                row.cells["vibix"] = Cell(False, reason=str(exc))
                row.reason = reason("player_failed", player="Vibix", cause=str(exc))
            return

        position, refusal = pick_season(playlist, choice.vibix_season)
        if position is None:
            for row in targets:
                refuse(row, refusal)
            return

        by_number = {episode.number: episode for episode in playlist if episode.season_index == position}
        to_ask: list[Row] = []
        for row in targets:
            episode = by_number.get(row.number)
            if episode is None or not episode.streams:
                refuse(row, reason("vibix_no_episode"))
                continue
            link = vibix_link(row.page_url, position, row.number)
            declared = max(stream.quality for stream in episode.streams)
            if declared <= row.best(off):
                # Cannot outrank a better source, so a manifest request is wasted.
                row.cells["vibix"] = Cell(True, declared, link)
                continue
            row.refs["vibix"] = PlayerRef("vibix", link)
            to_ask.append(row)
        await table.fill(self.vibix, http, self._probe_pacer, rows=to_ask)

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Read only the release page; probe its preview players for a quick report."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._site.read_release(url, http, headers)
        raw_text = resp.text

        name_ru, name_en = release_names(raw_text, slug)
        episodes_raw = release_episodes(raw_text)

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
        resp, slug = await self._site.read_release(url, http, headers)
        numbers = sorted(int(ep["number"]) for ep in release_episodes(resp.text)
                         if ep.get("number") is not None)
        if not numbers:
            return {**empty, "reason": reason("title_no_videos", player="Vibix")}
        episode_url = f"https://1.ruchime.org/release/{slug}/episode-{numbers[0]}"
        page = await self._site.read_episode_page(http, episode_url, headers, asyncio.Semaphore(1))
        if page is None or page.status_code != 200:
            return {**empty, "reason": reason("page_unreachable")}
        if self.vibix.find(html.unescape(page.text), episode_url) is None:
            return {**empty, "reason": reason("page_no_player", player="vibix")}
        try:
            playlist, _key = await self.vibix.playlist(episode_url)
        except (VibixUnavailable, VibixProtocolError) as exc:
            logger.warning("Vibix catalog unavailable for %s: %s", episode_url, exc)
            return {**empty, "reason": reason("player_failed", player="Vibix", cause=str(exc))}
        sources = self._vibix_sources(playlist, self._page_url(url), choice)
        position, _refusal = pick_season(playlist, choice.vibix_season)
        selected = address_of(self._page_url(url), choice.with_season(position)) if position else None
        return {"sources": sources, "selected_source": selected, "reason": ""}

    async def coverage(self, url: str, http: httpx.AsyncClient,
                       progress: Callable[[int, int, str], None] | None = None) -> dict:
        """What each page player really gives: its episodes and the resolution of each, grouped by ranges.

        Every episode page is read and every player is asked, which is slow, so the add form asks for it
        apart from the parse and shows the progress. The choice of the address does not change the answer:
        it is read once per page, and a disabled player is only hidden by the form."""
        probes = [adapter for adapter in PLAYER_ORDER if not adapter.series_scope]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._site.read_release(url, http, headers)
        listed = release_episodes(resp.text)
        if not listed:
            return {"players": {}, "reason": reason("title_no_videos", player="Ruchime")}
        async with self._lock_of(slug):
            try:
                table = await self._survey(slug, listed, http, headers, probes, progress)
            except ValueError as exc:
                logger.warning("Coverage of %s not read: %s", slug, exc)
                return {"players": {}, "reason": str(exc)}
            table.expires_at = time.monotonic() + SURVEY_TTL_S
            self._tables[slug] = table
        rows = list(table.rows.values())
        players = views.coverage(rows, probes)
        for adapter in probes:
            causes = views.causes(rows, adapter.name)
            logger.info("Coverage of %s: %s shows on %d episodes and plays on %d%s", slug, adapter.name,
                        players[adapter.name]["present"], len(views.played(rows, adapter.name)),
                        f" ({describe(causes.most_common(1)[0][0])})" if causes else "")
        return {"players": players, "reason": ""}

    def _lock_of(self, slug: str) -> asyncio.Lock:
        """One release is read by one request at a time; the others wait for the table and do not read the site."""
        return self._table_locks.setdefault(slug, asyncio.Lock())

    async def _survey(self, slug: str, listed: list[dict], http: httpx.AsyncClient, headers: dict,
                      probes: list[Player], progress: Callable[[int, int, str], None] | None) -> ReleaseTable:
        """Read every episode page, then ask every page player on every episode that shows it."""
        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        total, done, last_emit = len(listed), 0, 0.0

        async def read_one(ep: dict) -> Row | None:
            nonlocal done, last_emit
            row = await self._site.read_episode(slug, slug, ep, http, headers, sem)
            done += 1
            now = time.monotonic()
            if progress is not None and (done == total or now - last_emit >= 0.5):
                last_emit = now
                progress(done, total, "pages")
            return row

        read = await asyncio.gather(*(read_one(ep) for ep in listed))
        rows = [row for row in read if row is not None]
        failed = [row for row in rows if row.page_failed]
        if failed:
            raise ValueError(reason("pages_unread", site="Ruchime", count=len(failed), total=len(rows)))
        if progress is not None:
            progress(total, total, "players")
        table = ReleaseTable(slug, rows)
        for adapter in probes:
            await table.fill(adapter, http, self._probe_pacer)
        return table

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

    async def _quick_report(self, raw_text: str, page_url: str,
                            http: httpx.AsyncClient) -> tuple[dict, int | None]:
        """Probe the page's preview players; Vibix is checked on scan."""
        text = html.unescape(raw_text)
        refs = {adapter.name: adapter.find(text, page_url) for adapter in PLAYER_ORDER}
        tasks = []
        adapters: list[Player] = []
        for adapter in PLAYER_ORDER:
            if adapter.series_scope or refs[adapter.name] is None:
                continue
            adapters.append(adapter)
            tasks.append(probe(adapter, refs[adapter.name], http, page_url, self._probe_pacer))
        answers = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

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
            answer = answers[adapters.index(adapter)] if tasks else None
            if isinstance(answer, Exception) or answer is None:
                report[adapter.name] = {"present": True, "available": False,
                                        "resolution": None,
                                        "reason": reason("probe_failed", error=str(answer))}
                continue
            report[adapter.name] = {
                "present": True,
                "available": answer.available,
                "resolution": answer.resolution,
                "reason": answer.reason,
            }
            if answer.available and answer.resolution is not None:
                resolutions.append(answer.resolution)
        return report, (max(resolutions) if resolutions else None)

    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict,
                            episodes: list[int] | None = None,
                            progress: Callable[[int, int, str], None] | None = None) -> dict:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp, slug = await self._site.read_release(url, http, headers)

        choice = choice_of(url)
        active = [adapter for adapter in PLAYER_ORDER if adapter.name not in choice.off]
        if not active:
            return {"videos": [], "players": views.availability_report([], PLAYER_ORDER, choice.off, _PRIORITY)}

        all_episodes = release_episodes(resp.text)
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

        async with self._lock_of(slug):
            rows = await self._rows_for(slug, name_ru, episodes_raw, http, headers, progress)
            failed = [row for row in rows if row.page_failed]
            if failed:
                raise ValueError(reason("pages_unread", site="Ruchime", count=len(failed), total=len(rows)))
            if progress is not None:
                progress(len(rows), len(rows), "players")
            table = ReleaseTable(slug, rows)
            # The first player is asked on every episode; the next ones only where a better stream is still possible.
            for index, adapter in enumerate(active):
                if index and rows and all(row.best(choice.off) >= RESOLUTION_CEILING for row in rows):
                    break
                if adapter.series_scope:
                    await self._apply_vibix(table, rows, http, choice)
                else:
                    targets = rows if index == 0 else self._needs_lower_source(rows, adapter.name, choice.off)
                    await table.fill(adapter, http, self._probe_pacer, rows=targets)

        found, skipped = views.facts(rows, choice.off, _PRIORITY)
        for row in skipped:
            if row.unverified(choice.off):
                logger.warning("Skipping episode %s: source not confirmed (%s)", row.page_url, describe(row.reason) or "no data")
            else:
                logger.warning("Skipping episode %s: no playable player (%s)", row.page_url, describe(row.reason) or "no source")
        report = views.availability_report(rows, PLAYER_ORDER, choice.off, _PRIORITY)
        logger.info("Ruchime scan %s: серий %d, источники %s", slug, len(found), report["sources"])
        if progress is not None:
            progress(len(rows), len(rows), "done")
        return {"videos": found, "players": report}

    async def _rows_for(self, slug: str, name_ru: str, episodes_raw: list[dict], http: httpx.AsyncClient, headers: dict,
                        progress: Callable[[int, int, str], None] | None) -> list[Row]:
        """The rows of the episodes a scan needs: what the coverage has just read is taken, the rest is read once."""
        kept = self._tables.get(slug)
        kept = kept if kept is not None and kept.fresh() else None
        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        total, done, last_emit = len(episodes_raw), 0, 0.0

        async def read_one(ep: dict) -> Row | None:
            nonlocal done, last_emit
            cached = kept.rows.get(ep.get("number")) if kept is not None else None
            if cached is not None and not cached.page_failed:
                row = replace(cached, title=episode_title(name_ru, ep), refs=dict(cached.refs), cells=dict(cached.cells))
            else:
                row = await self._site.read_episode(slug, name_ru, ep, http, headers, sem)
            done += 1
            now = time.monotonic()
            if progress is not None and (done == total or now - last_emit >= 0.5):
                last_emit = now
                progress(done, total, "pages")
            return row

        return [row for row in await asyncio.gather(*(read_one(ep) for ep in episodes_raw)) if row is not None]

    async def resolve_stream(self, url: str, http: httpx.AsyncClient,
                             max_height: int | None = None) -> dict | None:
        """Resolve VK player links, AllVideo embeds and Vibix episode references."""
        if _PLAYERS["vk"].owns(url):
            return await self._resolve_vk(url, http, max_height)
        if any(domain in url for domain in ("fsst.online", "incvideo1.online")):
            return await self._resolve_allvideo(url, http)
        if self.match_url(url) and _EPISODE_IN_URL.search(url):
            return await self._resolve_vibix(url, http)
        return None

    async def _resolve_vk(self, url: str, http: httpx.AsyncClient, max_height: int | None) -> dict:
        """A player link with its key -> the address of one file now: the addresses of the files expire, so they are not kept."""
        player = _PLAYERS["vk"]
        ref = player.ref_of(url)
        verdict = await settle(player.with_files(), ref, http, SITE_ROOT, pacer=self._probe_pacer)
        probe = probe_from(verdict, ref, "VK")
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None:
            raise StreamUnavailable(probe.reason or reason("player_unconfirmed", player="VK"),
                                    transient=probe.transient or probe.available)
        return {"stream_url": picked[1], "headers": {}, "progressive": True}

    async def _resolve_allvideo(self, url: str, http: httpx.AsyncClient) -> dict | None:
        return await _PLAYERS["allvideo"].resolve(url, http)

    async def _resolve_vibix(self, url: str, http: httpx.AsyncClient) -> dict:
        """Episode page URL -> local manifest; a page is no stream, so failure is explicit."""
        match = _EPISODE_IN_URL.search(url)
        if not match:
            raise StreamUnavailable(reason("player_failed", player="Vibix", cause=reason("link_no_episode")))
        try:
            return await self.vibix.resolve(url, http, slug=self._extract_slug(url), number=int(match.group(1)))
        except VibixFailure as exc:
            raise StreamUnavailable(exc.reason, transient=exc.transient) from exc
