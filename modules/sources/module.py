"""Модуль sources — источники раздач: парсеры трекеров (Р-9). Таблицы модуля: `trackers`, `release_tables` и
`release_rows` (страницы сайтов), `provider_manifest` (последний манифест поставщика розетки, Р-102).

Queries:
  sources.parse {url}            → {service, title, releases[]} — the releases of a tracker; a page of a site
                                   (ruchime, dunhuatv, animakima, animesss, pvashow, yummyani, a VK channel) is read by
                                   `sources.table.get`, not parsed
  sources.torrent_file.get {url, torrent_id} → {content_b64} (Р-1, с кэшем)
  sources.torrent_file.drop {torrent_id} — чистка кэша (каскад Р-19)
  sources.trackers.list          → [{...}] (владелец таблицы trackers)

Tables of site pages (the one way to read a site page; sites are registered in `sites`, the core is `table.py`):
  sources.table.get {url, refresh?, rows?, by_sync?} → TableGetReply {url, state: ready | reading | failed | absent,
                                   table?, reason?}. A complete table within the term (term.py) is `ready`. Otherwise the page is read — by one
                                   task however many ask — and the reply is `reading` at once, with the last
                                   complete table when there is one; the end comes as the events below.
                                   `failed` in the reply means no read began (`site_not_found`: no site for the
                                   address; `provider_not_connected`: the provider that reads it is not up, and
                                   «Перечитать» of it is answered by the event `sources.table.failed` with the same reason). `url` of the reply is the canonical address the site gives: the key
                                   of the table and the `url` of the events. `refresh` false reads nothing and
                                   says how the page stands: `reading` while a read goes, `failed` (with the
                                   reason) when the last read failed, `ready` for a complete table however old,
                                   `absent` when nothing was read; with the last complete table when there is one.
                                   `rows` is how many rows of each line of an application card a read takes (the
                                   setting of the source). The page of an application card waits for the user: it
                                   is begun by `sources.table.read` or by a request with `by_sync` (a
                                   synchronization's own) that finds a table already read; any other request
                                   (a window, a preview, the recount of a layout) says how the page stands and
                                   begins nothing, however old its table is.
  sources.table.read {url, rows?} command: read the page now, term or not, unless a read of it is going; the read takes
                                   no raw data that an earlier read left for sharing (the listing of a VK channel)
  sources.table.cancel {url} → {cancelled}: the read of the page going now stops (the user stopped the sync that waits
                                   for it): it ends failed (`read_cancelled`) and is not begun again at the start; the
                                   last complete table still serves. False when no read of it goes.
  sources.video.fetch {url, target, max_height?, connections} → {ok, seconds, longest, height} | {ok: false, error,
                                   final}: the file of a link whose player downloads it itself, into `target`; how far
                                   it is goes as `sources.video.progress {target, phase, progress, ...}`. A refusal the
                                   player knows to be final (no such file) is `final`.
  sources.video.fetch.stop {target} → {stopped}: that download stops (its owner gave the file up).
  sources.site.resolve {url} → {site, canonical_url}: which site reads the address and the canonical
                                   form of it (no network); both null when no site does
  sources.settings.get {} → SourcesSettingsView {trackers: [{id, display_name, mirrors}], readers: [{name, kind:
                                   tracker | site, domains}]}: the settings screen of the sources whole — the mirrors of
                                   each tracker, and the domains the service reads pages of now: every tracker with its
                                   domains and mirrors, every site of the registry with its own (no network)
  sources.video.stream {url, max_height?} → {handled, stream_url, headers, progressive, playlist} | {handled, error,
                                   transient}: the stream of a candidate link, asked for right before the download;
                                   `player_fetch` instead: the player downloads the file itself (sources.video.fetch).
                                   The player that owns the link (a VK player link with its key, an AllVideo embed,
                                   a Vibix entry, an item of CDN VideoHub or of Kodik) resolves it; any other
                                   address is not handled
  sources.players.list PlayersListRequest {urls} → PlayersListReply {players: [PlayerFacts]}: every player a row of a
                                   table can be of, as the order of sources names it, with where it is among the pages
                                   of `urls` (their stored tables, no network): its kind (`site`, `line`), the best
                                   height of its rows and its places — the sites whose pages hold its rows, or the
                                   places a provider of the socket says it is in, each with how many of the pages are
                                   its. The players are those of the registry and those the manifest of the provider
                                   names
  sources.titles.search {query, page?} → TitleSearchReply {titles: [TitleCard], page, pages?, total?, notes, reason?}: a page of
                                   the search of the titles of the provider of the socket (contracts/provider_socket.md), as the
                                   provider gives it, or the reason it gave none (`provider_unavailable` when it cannot be
                                   reached). A title picked is a source by the address the provider gives
                                   (`title://<site>/<id>`). With the provider not up (none configured, not heard yet, dropped,
                                   another protocol) the search is refused: NotAllowed `provider_not_connected`
  sources.provider.get {} → ProviderView: the provider of external sources as the tab of the extensions shows it — its address,
                                   whether a token is set (never the token), the state of the socket and why it is not up, what its
                                   last manifest says (id, version, protocol, revision), its applications with the sources of the
                                   series and the lines each has, and the folder it writes the downloads to (inside the work folder of
                                   the alignment, asked of `conform.workspace`)
  sources.provider.set ProviderSettingsDraft {url, token?} → ProviderSaved {saved, problems, provider}: the address (empty: none) and
                                   the token (absent: the stored one stays, empty: cleared) checked as a whole, written to the settings
                                   `provider.url` and `provider.token` (the token encrypted) and taken at once, without a restart: the
                                   feed of the old provider ends, its sites and players leave the registries, the new one's join. The
                                   environment (`ST_PROVIDER_URL`, `ST_PROVIDER_TOKEN`) stands while no address is stored; a stored empty
                                   one is a deliberate «none»
  sources.pages.check {urls} → {blocked: [{url, reason}]}: the pages among `urls` that cannot be read now because the
                                   provider that reads them is not up (`provider_not_connected`, `provider_incompatible`).
                                   A page of the provider's world is one a site of its manifest reads, or an address that
                                   is no web address and no site reads (modules/sources/provider/gate.py). Scan asks it
                                   before it opens a sync of a series, whatever else the series is made of
  sources.terms.get {} → {sites: [{site, app, sources, minutes}]}: every site some source of a series reads (not a
                                   tracker: its table is the release `torrents` keeps), whether it is an application, how
                                   many sources read it and the term of what it answered (term.py), minutes
  sources.tables.fresh {urls} → {pages: [{url, read_at, fresh_until}]}: when the tables of the pages (canonical) were
                                   read and until when their term holds; a page with no table read is left out
  sources.terms.set {site, minutes} → {ok, minutes?, reason?}: the term of one site is stored (`sources.terms` of the
                                   settings) and is the term of every reader of that site at once; minutes from one to a
                                   month, any other value is refused (`sources_term_invalid`)
Events (owner: sources):
  sources.table.progress {url, phase, current, total}   how far a read is
  sources.table.ready {url, read_at, fresh_until}       a complete table is stored and served, current until fresh_until
  sources.table.fresh {url, read_at, fresh_until}       the term of the site of the page changed: the end of its term moved
  sources.settings.changed SourcesSettingsView          the mirrors of a tracker were saved: every open settings
                                                        screen shows them without asking back
  sources.table.failed {url, reason}                    the read failed; the last complete table still serves
  sources.pages.forgotten {urls}                        the tables of these pages are gone: no source lists them
  sources.table.trace {url, site, kind: begin | end, …}  what a read does, for its dump (modules/runlog): `end` carries
                                                        the seconds of the read and what every player of the page gave
                                                        (rows by state, frames, the causes of the rows without a video)
  sources.provider.changed ProviderView            the whole picture of the provider, told again whenever the socket, the manifest, the
                                                        address or the pages of the sources change (`seq` grows with every one); the
                                                        screen takes it as it is
  sources.provider.state {provider, state: connecting | up | down | incompatible, revision, reason}  how the socket of the
                                                        provider stands (modules/sources/provider; absent when none is configured)
  sources.provider.manifest {provider, revision}        the manifest of the provider was applied: its sites and players are those
                                                        of this revision
Events in: series.source.added | .updated {changed has url} | .removed, series.deleted, system.started — the tables of pages
belong to the sources of the series: the module asks the catalog which pages its sources list (`catalog.series.list`) and
forgets the table of a page that was listed and is not any more at once, and of a page that never was (a preview, one left
from before) when its table is past the term; a read of it that goes is stopped first. `series.deleted` is answered with
`series.purged {owner: sources}` (the cascade of Р-19).
settings.changed {key: sources.terms} — the terms of the sites (term.py): the tables of pages, the listings of VK channels
and the addresses parsers of applications gave are current for the term of their site; every stored table of a site whose
term moved is told again (`sources.table.fresh`).
settings.changed {key: voices.players_off} — the players the owner switched off for every source: a read asks none of
them (ReadContext.off) and keeps their rows of the last read as they were; a player switched on is asked at the next
read of a page (the term, or a read the user asks for).
A failed read is not retried by itself and a consumer does not ask again on `failed`: the next request after a
failure begins a new read. Only a complete table is ever published. A stop in the middle of a read is no verdict:
the state `reading` is stored and `on_start` begins the read again.

Доставка — ТОЛЬКО через `delivery.Delivery` (единственный выход
наружу): она перебирает хосты-кандидаты (канонический хост ссылки →
зеркала трекера → хост как написан) и проверяет, что приехало ожидаемое.
Транспорт выбирается по трекеру: kinozal / rutracker — trackerauth
(сессии и челлендж — не наша забота); anilibria — официальный API через
httpx; astar / anilibria_tv — общий Playwright-пул (_browser_html,
ленивый запуск, контекст на запрос).

Между запросами к одному трекеру выдерживается пауза (уважение к
чужому сервису) — обеспечивается замком на сервис + минимальным
интервалом.
"""
from __future__ import annotations

import json
import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Callable, Iterable


from core import BaseModule, compute, ids
from core import storage
from core.contracts.torrent import PageReleases
from core.contracts.release_table import site_of
from core.contracts.voices import PLAYERS_OFF_KEY
from core.contracts import (NotAllowed, ParsedDomains, PlayerFacts, PlayerPlace,
                            PlayersListReply, PlayersListRequest, SiteResolveRequest,
                            SourceType, SourcesSettingsView, TableGetRequest, TitleSearchReply, TitleSearchRequest, TrackerMirrors)
from core.deadlines import TABLE_READ_S
from core.db import Database
from core.envelope import Envelope
from core.http_limits import bounded_client
from core.logging import get_logger
from core.reasons import describe, reason

from . import anilibria, delivery, parsers
from .browser_pool import BrowserPool, chromium
from .delivery import Delivery
from .parsers import SourceParseError
from .provider import Connector, ProviderClient, ProviderHost, is_web_address, not_connected
from .provider import block as provider_block
from .releases import page_release
from .repository import SourcesRepository
from .term import DEFAULT_MINUTES, TERMS_KEY, Term, minutes_of, terms_of
from .players.alloha_session import Profile as AllohaProfile
from .players.contract import StreamUnavailable, TransferRefused
from .players.registry import PlayerRegistry
from .players.streams import resolve_stream
from .sites import Site, SiteError, SiteRegistry
from .sites.builtin import builtin_sites
from .table import FRESH_TOPIC, TableReader, utc_now

REQUEST_MIN_INTERVAL = 3.0  # секунд между запросами к одному трекеру
VK_API_BASE = "https://api.vk.com/method/"



class SourcesModule(BaseModule):
    name = "sources"

    # Браузерная доставка astar/anilibria_tv (Р-9). UA/viewport — как в
    # старом firefox-парсере (защита трекеров фингерпринтит браузер).
    _BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                   "Gecko/20100101 Firefox/125.0")
    BROWSER_TIMEOUT = 30000  # мс на загрузку/JS-челлендж в браузере

    def __init__(self, bus, db: Database, *,
                 torrent_cache_dir: str = "torrent_cache",
                 sites: Iterable[Site] | None = None,
                 table_clock: Callable[[], datetime] = utc_now,
                 table_safeguard_s: float = TABLE_READ_S, read_recent: int | None = None,
                 alloha_profile: AllohaProfile | None = None, provider: ProviderClient | None = None,
                 provider_reconnect: tuple[float, float] | None = None) -> None:
        self.repo = SourcesRepository(db)
        # The downloads players make themselves, by their files (`sources.video.fetch`).
        self._transfers: dict[str, asyncio.Future] = {}
        self._cache_dir = torrent_cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        # The one browser of the module, started at the first page that needs it and stopped after its last user
        # (browser_pool.py): the players and the tracker pages take their contexts of it.
        self.browsers = BrowserPool(get_logger("sources"))
        self.alloha_browsers = BrowserPool(get_logger("sources"), launch=chromium)
        wiring = dict(
            browser_getter=self.browsers.browser,
            key_path=os.path.abspath(os.path.join(torrent_cache_dir,
                                                  "vibix-key.json")),
            manifest_dir=os.path.abspath(os.path.join(torrent_cache_dir,
                                                      "manifests")),
            alloha_profile=alloha_profile)
        # One object per player for the whole module: the sites read with them and the streams are resolved by them.
        self.players = PlayerRegistry.standard(**wiring)
        self.players.get("alloha").configure(
            alloha_browser_getter=self.alloha_browsers.browser,
            alloha_profile_path=os.path.abspath(os.path.join(torrent_cache_dir, "alloha-profile.enc")))
        # The terms of what the sources answered, by the site, for every reader of the module; the setting changes them for
        # all at once.
        self.term = Term()
        # The pages some source lists, as the catalog said at the last change of the sources (None until it was asked), and
        # the lock that makes one such change one step: the topics of the change are taken by tasks of their own.
        self._listed: set[str] | None = None
        self._listing = asyncio.Lock()
        # The players switched off for every source (the setting of the voices module, kept here as the event says it).
        self.players_off: frozenset[str] = frozenset()
        # The sites belong to this instance, not to the process: a site is added by registering it (builtin.py).
        self.sites = SiteRegistry(builtin_sites() if sites is None else sites)
        self.sites.configure(players=self.players, vk_call=self._vk_call, term=self.term)
        # The provider of the socket (modules/sources/provider), when the environment names one: its sites and players join
        # the registries as its manifest says, and leave them as it says.
        self.provider = ProviderHost(
            repo=self.repo, sites=self.sites, players=self.players, term=self.term,
            publish=lambda topic, payload: self.publish_event(topic, payload),
            spawn=lambda coro, **kw: self.spawn(coro, **kw), request=lambda topic, payload, **kw: self.request(topic, payload, **kw),
            site_of=lambda url: self.tables.resolve(url).site, default=provider, reconnect=provider_reconnect)
        self.tables = TableReader(
            self.repo, self.sites, lambda topic, payload: self.publish_event(topic, payload), lambda: self._http,
            players=self.players, clock=table_clock, safeguard_s=table_safeguard_s, recent=read_recent, term=self.term,
            holding=self.browsers.held, off=lambda: self.players_off, blocked=self._provider_block,
            ask=lambda topic, payload: self.request(topic, payload))
        super().__init__(bus)
        # Late binding on purpose: tests replace the transport, not the bus.
        self._delivery = Delivery(
            request=lambda topic, payload, **kw: self.request(topic, payload,
                                                              **kw),
            http=lambda: self._http,
            browser_html=lambda service, url: self._browser_html(service, url),
            log=self.log)

    def register(self) -> None:
        self.handle("sources.parse", self.on_parse)
        self.handle("sources.releases.get", self.on_releases_get)
        self.handle("unpack.table.changed", self.on_unpack_table_changed)
        self.handle("sources.torrent_file.get", self.on_torrent_file)
        self.handle("sources.torrent_file.drop", self.on_torrent_file_drop)
        self.handle("sources.trackers.list", self.on_trackers_list)
        self.handle("sources.tracker.set_mirrors", self.on_set_mirrors)
        self.handle("sources.video.stream", self.on_video_stream)
        self.handle("sources.video.fetch", self.on_video_fetch, concurrent=True)
        self.handle("sources.video.fetch.stop", self.on_video_fetch_stop)
        self.handle("sources.table.get", self.on_table_get)
        self.handle("sources.table.read", self.on_table_read)
        self.handle("sources.table.cancel", self.on_table_cancel)
        self.handle("sources.site.resolve", self.on_site_resolve)
        self.handle("sources.settings.get", self.on_settings_get)
        self.handle("sources.titles.search", self.on_titles_search)
        self.handle("sources.pages.check", self.on_pages_check)
        self.handle("sources.provider.get", self.on_provider_get)
        self.handle("sources.provider.set", self.on_provider_set)
        self.handle("sources.players.list", self.on_players_list)
        self.handle("sources.terms.get", self.on_terms_get)
        self.handle("sources.tables.fresh", self.on_tables_fresh)
        self.handle("sources.terms.set", self.on_terms_set)
        self.handle("settings.changed", self.on_settings_changed)
        # The tables of pages belong to the sources of the series: a page no source lists any more is forgotten.
        for topic in ("system.started", "series.source.added", "series.source.updated", "series.source.removed"):
            self.handle(topic, self.on_pages_listing)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_pages_listing(self, env: Envelope) -> None:
        """The service started or a source came, went or changed its address: the pages the sources list are counted
        again and the table of every page that is no longer listed goes (an update that changes no address changes
        nothing here)."""
        if env.topic == "series.source.updated" and "url" not in (env.payload.get("changed") or []):
            return
        await self._forget_unlisted_pages()
        self.provider.announce()  # how many sources each application has changed

    async def on_series_deleted(self, env: Envelope) -> None:
        """Cascade of a deleted series (Р-19): the tables of the pages only it listed go, and the owner reports it."""
        if await self._forget_unlisted_pages():
            self.publish_event("series.purged", {"series_id": env.payload["series_id"], "owner": self.name})

    async def _forget_unlisted_pages(self) -> bool:
        """Ask the catalog which pages its sources list and forget the stored pages that none does. False when the catalog
        could not be asked: nothing is forgotten then, and the next change counts again."""
        async with self._listing:
            try:
                series = await self.request("catalog.series.list", {})
                if not isinstance(series, list):
                    raise TypeError("the list of series is no list")
                addresses = [source["url"] for one in series for source in one.get("sources") or []]
                # A page some source lists is kept even when no site reads its address now (the provider of the socket is not
                # up, or is not configured): its table is the one the next read of it begins from.
                listed = self.tables.keys_of(addresses) | set(addresses)
                gone = await self.tables.forget(listed, self._listed or set())
            except Exception:  # noqa: BLE001 - the tables stay as they are; a table kept is no loss
                self.log.exception("the pages no source lists were not counted")
                return False
            self._listed = listed
            if gone:
                self.log.info("страницы без источника забыты вместе с таблицами: %d (%s)", len(gone),
                              ", ".join(sorted(gone))[:600])
                self.publish_event("sources.pages.forgotten", {"urls": sorted(gone)})
            return True

    async def on_start(self) -> None:
        self._http = bounded_client(timeout=20.0, follow_redirects=True)
        try:
            stored = (await self.request("settings.value.get", {"key": TERMS_KEY})).get("value")
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves every site at the default term
            stored = None
        self.term.minutes = terms_of(stored)
        try:
            players_off = (await self.request("settings.value.get", {"key": PLAYERS_OFF_KEY})).get("value")
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves every player asked, as the term stays at its default
            players_off = None
        self._set_players_off(players_off)
        # The sites of the saved manifest first: a provider that is down at the start must not take its pages' tables out of
        # the hands of its sites (the next step forgets the pages no site reads).
        await self.provider.start()
        await self._configure_trackers()
        try:
            await self.tables.resume()
        except Exception:  # noqa: BLE001 — the other duties of the module do not wait for the table reads
            # Consumers ask for their tables again when they start; a page left marked "reading" is read at the next ask.
            self.log.exception("reads of tables interrupted by a stop were not resumed")

    async def on_stop(self) -> None:
        await self.provider.stop()
        await self.tables.close()
        await self.players.get("alloha").close()
        await self._http.aclose()
        await self.browsers.shutdown()
        await self.alloha_browsers.shutdown()

    async def on_terms_get(self, env: Envelope) -> dict:
        """Every site some source of a series reads, with how many sources read it and its term."""
        counted: dict[str, int] = {}
        apps: set[str] = set()
        for series in await self.request("catalog.series.list", {}):
            for source in series.get("sources") or []:
                if source.get("source_type") == SourceType.TORRENT:
                    continue
                site = site_of(source["url"])
                if site:
                    counted[site] = counted.get(site, 0) + 1
                    if not is_web_address(source["url"]):
                        apps.add(site)
        order = sorted(counted, key=lambda site: (site in apps, -counted[site], site))
        return {"sites": [{"site": site, "app": site in apps, "sources": counted[site],
                           "minutes": self.term.minutes.get(site, DEFAULT_MINUTES)} for site in order]}

    async def on_tables_fresh(self, env: Envelope) -> dict:
        """{urls} → {pages: [{url, read_at, fresh_until}]}: when the tables of the pages were read and until when they are
        current (the window shows it on each source; the events ready and fresh carry it on)."""
        pages = await self.tables.fresh_of([str(url) for url in env.payload.get("urls") or []])
        return {"pages": [page.model_dump(mode="json") for page in pages]}

    async def on_terms_set(self, env: Envelope) -> dict:
        site, minutes = str(env.payload.get("site") or ""), minutes_of(env.payload.get("minutes"))
        if not site or minutes is None:
            return {"ok": False, "reason": reason("sources_term_invalid", value=str(env.payload.get("minutes")))}
        await self.request("settings.value.set", {"key": TERMS_KEY,
                                                   "value": json.dumps({**self.term.minutes, site: minutes}, sort_keys=True)})
        return {"ok": True, "minutes": minutes}

    async def on_settings_changed(self, env: Envelope) -> None:
        if env.payload.get("key") == TERMS_KEY:
            before, self.term.minutes = self.term.minutes, terms_of(env.payload.get("value"))
            for site in {site for site in before.keys() | self.term.minutes.keys()
                         if before.get(site, DEFAULT_MINUTES) != self.term.minutes.get(site, DEFAULT_MINUTES)}:
                for fresh in await self.tables.fresh_of_site(site):
                    self.publish_event(FRESH_TOPIC, fresh.model_dump(mode="json"))
        elif env.payload.get("key") == PLAYERS_OFF_KEY:
            self._set_players_off(env.payload.get("value"))

    def _set_players_off(self, value) -> None:
        """The players a setting names: a JSON list of names; anything else leaves every player asked."""
        try:
            names = json.loads(value) if value not in (None, "") else []
        except (TypeError, ValueError):
            names = None
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            self.log.warning("отключённые плееры «%s»: не список имён, опрашиваются все", value)
            names = []
        self.players_off = frozenset(names)

    # --- бережный доступ к трекеру ------------------------------------------------

    async def _polite(self, service: str) -> None:
        since = time.monotonic() - self._last_request.get(service, -1e9)
        if since < REQUEST_MIN_INTERVAL:
            await asyncio.sleep(REQUEST_MIN_INTERVAL - since)
        self._last_request[service] = time.monotonic()

    # --- queries --------------------------------------------------------------------

    async def on_trackers_list(self, env: Envelope) -> list[dict]:
        return await self.repo.all_trackers()

    async def on_set_mirrors(self, env: Envelope) -> dict:
        """Зеркала трекера (вкладка настроек, Р-22)."""
        await self.repo.set_mirrors(env.payload["tracker_id"],
                                    env.payload["mirrors"])
        await self._configure_trackers()
        self.publish_event("sources.settings.changed", await self._settings())
        return {"ok": True}

    async def on_torrent_file_drop(self, env: Envelope) -> None:
        """Каскад Р-19: удаление серии чистит кэш её .torrent-файлов
        (команда от torrents — torrent_id знает только он)."""
        import os
        path = self._cache_path(env.payload["torrent_id"])
        await storage.call(
            lambda: os.path.isfile(path) and os.remove(path), owns=path)

    async def _configure_trackers(self) -> None:
        """The sites of the trackers read the addresses of the mirrors the settings hold (table `trackers`)."""
        try:
            trackers = await self.repo.all_trackers()
        except Exception:  # noqa: BLE001 - the sites keep their own domains; the next change of the mirrors tries again
            self.log.exception("the mirrors of the trackers were not read")
            return
        self.sites.configure(tracker_mirrors={t["canonical_name"]: t["mirrors"] for t in trackers})

    async def on_releases_get(self, env: Envelope) -> dict:
        """payload: {url}; reply: PageReleases — the releases the page of a tracker lists now, read from the tracker
        (stage 15): `release` is the id of a release on the page, the same for every version of it."""
        url = env.payload["url"]
        site = self.tables.resolve(url)
        parsed = await self.on_parse(env)
        page = PageReleases(url=site.canonical_url or url, site=parsed["service"],
                            title=(parsed.get("title") or {}).get("ru") or "",
                            releases=[page_release(parsed["service"], one) for one in parsed.get("releases") or []])
        return page.model_dump(mode="json")

    async def on_unpack_table_changed(self, env: Envelope) -> None:
        """The tracks of a tracker's page changed (a release downloaded, read, updated, laid out anew): its table is made
        again from them; the end comes as `sources.table.ready` as for any page."""
        await self.tables.read(TableGetRequest(url=env.payload["url"]), changed=True)

    async def on_parse(self, env: Envelope) -> dict:
        url = env.payload["url"]
        tracker = await self.repo.resolve(url)
        if not tracker:
            raise SourceParseError(reason("tracker_unknown_url", url=url))
        service = tracker["canonical_name"]
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            await self._polite(service)
            result = await self._parse_by_service(service, url,
                                                  tracker["mirrors"])
        # torrent_id — констрейнт данных (core/ids, Р-10/Р-22); считаем
        # здесь один раз, scan и форма добавления используют готовый
        for r in result.get("releases", []):
            link_for_id = r.get("link") or r.get("magnet")
            if link_for_id and "torrent_id" not in r:
                r["torrent_id"] = ids.torrent_id(link_for_id,
                                                 r.get("date_marker"))
        result["service"] = service
        result["ui_features"] = tracker["ui_features"]
        result["tracker"] = tracker
        return result

    async def _parse_by_service(self, service: str, url: str,
                                mirrors: list[str]) -> dict:
        if service == "anilibria":
            alias = anilibria.alias_from_url(url)
            api = anilibria.api_base_from_url(url)
            delivered = await self._delivery.fetch(
                service, f"{api}/anime/releases/{alias}", expect="json",
                mirrors=mirrors, transport="http",
                accept=lambda data, _url: anilibria.release_to_result(data))
            return delivered.value

        if service in ("kinozal", "rutracker"):
            parse = (parsers.kinozal_parse if service == "kinozal"
                     else parsers.rutracker_parse)
            return await self._parse_page(service, url, mirrors, parse,
                                          transport="auth")

        if service in ("astar", "anilibria_tv"):
            parse = (parsers.astar_parse if service == "astar"
                     else parsers.anilibria_tv_parse)
            return await self._parse_page(service, url, mirrors, parse,
                                          transport="browser")

        raise SourceParseError(reason("tracker_no_impl", service=service))

    # --- the browser pool (astar/anilibria_tv, Р-9): modules/sources/browser_pool.py ---------

    async def _browser_html(self, service: str, url: str) -> str:
        """HTML страницы трекера через браузер. Подготовка страницы
        зависит от трекера (порт старых парсеров): astar — открыть список
        кликом «Все торренты»; anilibria_tv — дождаться таблицы после
        JS-челленджа."""
        browser = await self.browsers.browser()
        context = await browser.new_context(
            user_agent=self._BROWSER_UA,
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True)
        try:
            page = await context.new_page()
            await page.goto(url, timeout=self.BROWSER_TIMEOUT,
                            wait_until="domcontentloaded")
            if service == "astar":
                await page.wait_for_selector("span#torrent_all",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
                await page.click("span#torrent_all")
                await page.wait_for_selector("div.list_torrent",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
            elif service == "anilibria_tv":
                await page.wait_for_selector("table#publicTorrentTable",
                                             state="visible",
                                             timeout=self.BROWSER_TIMEOUT)
            return await page.content()
        finally:
            await context.close()

    async def _parse_page(self, service: str, url: str,
                          mirrors: list[str], parse, *,
                          transport: str) -> dict:
        """Страница трекера + разбор. Разбор идёт ВНУТРИ попытки
        доставки, поэтому страница, которую не удалось разобрать,
        означает переезд на следующий хост, а не отказ. Результат несёт
        фактический URL ('url')."""
        delivered = await self._delivery.fetch(
            service, url, expect="page", mirrors=mirrors,
            transport=transport,
            accept=lambda html, attempt_url: parse(html, attempt_url))
        return {**delivered.value, "url": delivered.url}

    # --- VK API for the sites (finding 15: the token lives in trackerauth) -------------------

    async def _vk_call(self, method: str, params: dict) -> dict:
        """One call of the VK API through the delivery: the answer, or a `SiteError` with the reason the site reports."""
        try:
            delivered = await self._delivery.fetch(
                "vk", f"{VK_API_BASE}{method}", expect="json",
                transport="auth", params=params)
        except SourceParseError as exc:
            raise SiteError(reason("vk_call_failed", method=method, cause=str(exc))) from exc
        data = delivered.value
        if "error" in data:
            raise SiteError(reason("vk_api_error", method=method, message=data["error"].get("error_msg"),
                                   error_code=data["error"].get("error_code")))
        return data

    async def on_table_get(self, env: Envelope) -> dict:
        """payload: TableGetRequest; reply: TableGetReply. Never waits for a read: the end of it comes as an event."""
        reply = await self.tables.get(TableGetRequest.model_validate(env.payload))
        # The table of a page is thousands of rows: it is made into the message off the loop of the service.
        return await compute.run(reply.model_dump, mode="json")

    async def on_table_read(self, env: Envelope) -> None:
        await self.tables.read(TableGetRequest.model_validate(env.payload))

    async def on_table_cancel(self, env: Envelope) -> dict:
        """The read of a page stopped by the user (a sync stopped): it ends failed, the last table serves."""
        return {"cancelled": await self.tables.cancel(str(env.payload["url"]))}

    async def on_settings_get(self, env: Envelope) -> dict:
        return await self._settings()

    async def _settings(self) -> dict:
        stored = await self.repo.all_trackers()
        trackers = [TrackerMirrors(id=each["id"], display_name=each["display_name"], mirrors=list(each.get("mirrors") or []))
                    for each in stored]
        readers = [ParsedDomains(name=each.display_name, kind="tracker", domains=each.mirrors) for each in trackers]
        # A tracker is listed once, as a tracker, though its page is read by a site of the registry too.
        readers += [ParsedDomains(name=site.name, kind="site", domains=list(site.domains)) for site in self.sites
                    if site.source_type != SourceType.TORRENT.value]
        return SourcesSettingsView(trackers=trackers, readers=readers).model_dump(mode="json")

    async def on_site_resolve(self, env: Envelope) -> dict:
        """payload: SiteResolveRequest; reply: SiteResolveReply. No network."""
        request = SiteResolveRequest.model_validate(env.payload)
        return self.tables.resolve(request.url).model_dump(mode="json")

    async def on_players_list(self, env: Envelope) -> dict:
        """Every player a row of a table can be of, with the words a window names it by and where it is among the pages
        asked about: the players of the registry, and those the manifest of the provider names (`line:<code>` of the
        video applications), each in every place the manifest says it is."""
        request = PlayersListRequest.model_validate(env.payload or {})
        urls = list(dict.fromkeys(self.tables.resolve(url).canonical_url or url for url in request.urls))
        pages, heights = await self.repo.players_of_pages(urls)

        def places(player: str, admitting: Iterable = ()) -> list[PlayerPlace]:
            found = {place.name: PlayerPlace(name=place.name, label=place.label, sources=pages.get((player, place.name), 0))
                     for place in admitting}
            labels = self.connector.labels() if self.connector else {}
            for (name, site), count in pages.items():
                if name == player and site not in found:
                    found[site] = PlayerPlace(name=site, label=labels.get(site, site), sources=count)
            return sorted(found.values(), key=lambda place: (-place.sources, place.label))

        players = [PlayerFacts(name=each.name, label=getattr(each, "label", "") or each.name, kind="site",
                               height=heights.get(each.name), places=places(each.name))
                   for each in self.players if not getattr(each, "lines", False)]
        for each in (self.connector.players() if self.connector else ()):
            players.append(PlayerFacts(name=each.name, label=each.label or each.name, kind=each.kind,
                                       height=heights.get(each.name), places=places(each.name, each.places)))
        return PlayersListReply(players=players).model_dump(mode="json")

    async def on_titles_search(self, env: Envelope) -> dict:
        """payload: TitleSearchRequest; reply: TitleSearchReply — a page of the search of the titles of the provider of the socket,
        as the provider gives it, or the reason it gave none. No provider, no search: the user is told."""
        request = TitleSearchRequest.model_validate(env.payload)
        if (why := not_connected(self.connector)) is not None:
            raise NotAllowed(why)  # the search is the provider's: with none up there is nothing to search, and the user is told
        return (await self.connector.search(request)).model_dump(mode="json")

    @property
    def connector(self) -> Connector | None:
        return self.provider.connector

    async def on_provider_get(self, env: Envelope) -> dict:
        return (await self.provider.view()).model_dump(mode="json")

    async def on_provider_set(self, env: Envelope) -> dict:
        return (await self.provider.save(env.payload)).model_dump(mode="json")

    def _provider_block(self, url: str) -> str | None:
        """The reason a page cannot be read now because the provider that reads it is not up; None for any other page."""
        return provider_block(self.connector, self.sites, url)

    async def on_pages_check(self, env: Envelope) -> dict:
        """payload: {urls}; reply: {blocked: [{url, reason}]} — the pages among them that cannot be read now for want of the
        provider that reads them (none configured, not heard yet, dropped, another protocol). A synchronization asks before
        it opens: a series with such a page is not synchronized, whatever its other pages are."""
        blocked = [{"url": url, "reason": why} for url in env.payload.get("urls") or []
                   if (why := self._provider_block(url)) is not None]
        return {"blocked": blocked}

    async def on_video_stream(self, env: Envelope) -> dict:
        """payload: {url, max_height?}
        reply: {handled, stream_url, headers, progressive, playlist, player_fetch, fetch_by?} | {handled, error, transient,
        unasked?}. `unasked`: the player was not reached (the provider is not up), so the error is nobody's failure.
        `progressive` marks one ready file that a server may be asked for in parts; `playlist` an HLS playlist that is
        downloaded by its segments; `player_fetch` that the player downloads the file itself (`sources.video.fetch`), and
        `fetch_by` `provider` that the player is a provider of the socket, which takes the whole file by its own way.

        `error` means the URL belongs to a player that has no stream for it:
        the caller must not hand the page to a generic downloader."""
        url, max_height = env.payload["url"], env.payload.get("max_height")
        try:
            stream_info = await resolve_stream(self.players, url, self._http, max_height)
        except StreamUnavailable as exc:
            return {"handled": True, "error": exc.reason,
                    "transient": exc.transient}
        if stream_info:
            return {
                "handled": True,
                "stream_url": stream_info["stream_url"],
                "headers": stream_info.get("headers", {}),
                "progressive": bool(stream_info.get("progressive")),
                "playlist": bool(stream_info.get("playlist")),
                "player_fetch": bool(stream_info.get("player_fetch")),
                **({"fetch_by": stream_info["fetch_by"]} if stream_info.get("fetch_by") else {}),
            }
        if not is_web_address(url):
            # A link of the provider's world no player owns: no generic downloader can open it. With the provider not up nobody
            # was asked (`unasked`: asked again later, never the source's failure); with it up the link is nobody's (`final`).
            if (why := not_connected(self.connector)) is not None:
                return {"handled": True, "error": why, "transient": True, "unasked": True}
            return {"handled": True, "error": reason("not_a_link_of_the_provider", url=url), "transient": False}
        return {"handled": False}

    async def on_video_fetch(self, env: Envelope) -> dict:
        """payload: {url, target, max_height?, connections}; reply: {ok, seconds, longest, height} | {ok: false, error,
        final, kind}. The file of a link whose player downloads it itself (`player_fetch` of the stream): the player takes the
        file into `target`, telling how far it is by `sources.video.progress {target, ...figures}`. `kind` of a refusal is
        `final` (the source's last word), `broken` (a way that broke) or `unasked` (the source was not reached)."""
        url, target = env.payload["url"], env.payload["target"]
        player = next((each for each in self.players if each.owns(url) and hasattr(each, "transfer")), None)
        if player is None:
            return {"ok": False, "error": reason("no_player_downloads"), "final": True, "kind": "final"}

        async def told(figures: dict) -> None:
            self.publish_event("sources.video.progress", {"target": target, **figures})

        work = asyncio.ensure_future(player.transfer(url, target, env.payload.get("max_height"),
                                                     int(env.payload.get("connections") or 1), told))
        self._transfers[target] = work
        try:
            done = await work
        except asyncio.CancelledError:
            if work.cancelled():
                return {"ok": False, "error": reason("download_stopped"), "final": False, "kind": "unasked"}
            raise
        except TransferRefused as exc:
            self.log.warning("%s: загрузка плеером не удалась: %s", os.path.basename(target), describe(str(exc)))
            return {"ok": False, "error": str(exc), "final": exc.final, "kind": exc.kind}
        finally:
            self._transfers.pop(target, None)
        return {"ok": True, **done}

    async def on_video_fetch_stop(self, env: Envelope) -> dict:
        """payload: {target}; the download of the player into that file stops (its owner gave the file up)."""
        work = self._transfers.get(env.payload["target"])
        if work is not None:
            work.cancel()
        return {"stopped": work is not None}

    # --- скачивание .torrent (Р-1) ----------------------------------------------------

    async def on_torrent_file(self, env: Envelope) -> dict:
        url, torrent_id = env.payload["url"], env.payload["torrent_id"]
        cached = await storage.call(self._cache_read, torrent_id)
        if cached is not None:
            return {"content_b64": base64.b64encode(cached).decode(),
                    "from_cache": True}

        tracker = await self.repo.resolve(url)
        service = tracker["canonical_name"] if tracker else ""
        mirrors = tracker["mirrors"] if tracker else []
        delivered = await self._delivery.fetch(
            service, url, expect="torrent", mirrors=mirrors,
            transport="auth" if service in ("kinozal", "rutracker") else "http",
            timeout=delivery.TORRENT_TIMEOUT)
        content = delivered.value

        await storage.call(self._cache_write, torrent_id, content, owns=self._cache_path(torrent_id))
        return {"content_b64": base64.b64encode(content).decode(),
                "from_cache": False}

    # --- кэш .torrent-файлов ------------------------------------------------------------

    def _cache_path(self, torrent_id: str) -> str:
        import os
        return os.path.join(self._cache_dir, f"{torrent_id}.torrent")

    def _cache_read(self, torrent_id: str) -> bytes | None:
        import os
        path = self._cache_path(torrent_id)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        return None

    def _cache_write(self, torrent_id: str, content: bytes) -> None:
        import os
        os.makedirs(self._cache_dir, exist_ok=True)
        with open(self._cache_path(torrent_id), "wb") as f:
            f.write(content)
