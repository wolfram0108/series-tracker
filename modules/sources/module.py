"""Module sources: the sources of the releases — the parsers of the trackers (Р-9). Tables of the module: `trackers`, `release_tables` and
`release_rows` (the pages of sites), `provider_manifest` (the last manifest of the provider of the socket, Р-102). The answers are the models
of core/contracts (immutable, handed over as they are); what is counted of them is calc/sources, what the module keeps is the store, the
sites, the tasks of the reads, the cache of the files and the one way out to the network (`delivery.py`).

Queries (the answer is the model):
  sources.parse {url} -> {service, title, releases[]}: the releases of a tracker; a page of a site (ruchime, dunhuatv, animakima, animesss,
      pvashow, yummyani, a VK channel) is read by the table topics below, not parsed. Nobody asks it over the bus: it leaves with the old
      torrent pipeline; `sources.releases.get` is its live user, inside the module. Its answer stays a dictionary.
  sources.releases.get PageUrl -> PageReleases: the releases the page of a tracker lists now, read from the tracker.
  sources.torrent_file.get TorrentFileGet -> TorrentFile {content_b64, from_cache}: the `.torrent` of a release (Р-1), from the cache of the
      module; a miss fetches it and writes the cache, which is no state another module reads, so it is a query.
  sources.trackers.list -> TrackersReply: the trackers (the owner of the table `trackers`).
  sources.table.get TableGetRequest -> TableGetReply {url, state: ready | reading | failed | absent, table?, reason?, fresh_until?}: the
      page as it stands, read from nothing: `reading` while a read goes, `failed` (with the reason) when the last read failed, `ready` for a
      complete table however old, `absent` when nothing was read; with the last complete table when there is one. `failed` with
      `site_not_found` (no site for the address) or `provider_not_connected` (the provider that reads it is not up) says no read could
      begin. `url` of the reply is the canonical address the site gives: the key of the table and the `url` of the events.
  sources.site.resolve SiteResolveRequest -> SiteResolveReply {site, canonical_url, source_type}: which site reads the address and its
      canonical form (no network); all none when no site does.
  sources.settings.get -> SourcesSettingsView {trackers, readers}: the settings screen of the sources whole — the mirrors of each tracker,
      and the domains the service reads pages of now: every tracker with its domains and mirrors, every site of the registry with its own.
  sources.players.list PlayersListRequest -> PlayersListReply {players: [PlayerFacts]}: every player a row of a table can be of, as the
      order of sources names it, with where it is among the pages of `urls` (their stored tables, no network): its kind (`site`, `line`,
      `torrent`), the best height of its rows and its places — the sites whose pages hold its rows, or the places a provider of the socket
      says it is in, each with how many of the pages are its. The players are those of the registry and those the manifest of the provider names.
  sources.titles.search TitleSearchRequest -> TitleSearchReply: a page of the search of the titles of the provider of the socket
      (contracts/provider_socket.md), as the provider gives it, or the reason it gave none (`provider_unavailable` when it cannot be reached).
      A title picked is a source by the address the provider gives (`title://<site>/<id>`). With the provider not up (none configured,
      dropped, another protocol) the search is refused: NotAllowed `provider_not_connected`; before the first look at the provider (its first
      hello, or its first failure) the request waits for it.
  sources.provider.get -> ProviderView: the provider of external sources as the tab of the extensions shows it — its address, whether a token
      is set (never the token), the state of the socket and why it is not up, what its last manifest says, its sites with the sources of the
      series and the lines each has, and the folder it writes the downloads to (asked of `conform.workspace`).
  sources.pages.check PagesCheck -> PagesCheckReply {blocked: [{url, reason}]}: the pages among `urls` that cannot be read now because the
      provider that reads them is not up (`provider_not_connected`, `provider_incompatible`). A page of the provider's world is one a site of
      its manifest reads, or an address that is no web address and no site reads (modules/sources/provider/gate.py); asked before the first
      look at the provider, the module waits for it (the same for the table topics of such a page, and the stream of a link of the provider).
      Scan asks it before it opens a sync of a series, whatever else the series is made of.
  sources.terms.get -> TermsReply {sites: [{site, provider, sources, minutes}]}: every site some source of a series reads (not a tracker: its
      table is the release `torrents` keeps), whether it is a site of the provider, how many sources read it and the term of what it answered.
  sources.tables.fresh TablesFresh -> TablesFreshReply {pages: [{url, read_at, fresh_until}]}: when the tables of the pages (canonical) were
      read and until when their term holds; a page with no table read is left out.
  sources.video.stream VideoStream -> StreamReply: the stream of a candidate link, asked for right before the download; `player_fetch`
      instead: the player downloads the file itself (sources.video.fetch). The player that owns the link (a VK player link with its key, an
      AllVideo embed, a Vibix entry, an item of CDN VideoHub or of Kodik) resolves it; any other address is not handled.
Commands (the answer, when there is one, is the model):
  sources.table.refresh TableRefresh -> TableGetReply: read the page if its table is not current, and answer how the page stands after that.
      A complete table within the term (calc/sources/term.py) is `ready`. Otherwise the page is read — by one task however many ask — and the
      reply is `reading` at once, with the last complete table when there is one; the end comes as the events below. `rows` is how many rows
      of each line of a title of the provider a read takes (the setting of the source). The page of a title of the provider waits for the user:
      it is begun by `sources.table.read` or by a request with `by_sync` (a synchronization's own) that finds a table already read; any other
      request (a window, a preview, the recount of a layout) says how the page stands and begins nothing, however old its table is.
  sources.table.read TableRead: read the page now, term or not, unless a read of it is going; the read takes no raw data that an earlier read
      left for sharing (the listing of a VK channel).
  sources.table.cancel TableCancel -> TableCancelled: the read of the page going now stops (the user stopped the sync that waits for it): it
      ends failed (`read_cancelled`) and is not begun again at the start; the last complete table still serves. False when no read of it goes.
  sources.terms.set TermSet -> TermSaved {ok, minutes?, reason?}: the term of one site is stored (`sources.terms` of the settings) and is the term
      of every reader of that site at once; minutes from one to a month, any other value is refused (`sources_term_invalid`).
  sources.tracker.set_mirrors MirrorsSet -> MirrorsSaved: the domains a tracker is read on; `sources.settings.changed` follows.
  sources.provider.set ProviderSettingsDraft -> ProviderSaved {saved, problems, provider}: the address (empty: none) and the token (absent: the
      stored one stays, empty: cleared) checked as a whole, written to the settings `provider.url` and `provider.token` (the token encrypted)
      and taken at once, without a restart: the feed of the old provider ends, its sites and players leave the registries, the new one's join.
      The environment (`ST_PROVIDER_URL`, `ST_PROVIDER_TOKEN`) stands while no address is stored; a stored empty one is a deliberate "none".
  sources.video.fetch VideoFetch -> VideoFetched {ok, seconds, longest, height} | {ok: false, error, final, kind}: the file of a link whose player
      downloads it itself, into `target`; how far it is goes as `sources.video.progress {target, phase, progress, ...}`. A refusal the player knows to
      be final (no such file) is `final`.
  sources.video.fetch.stop VideoFetchStop -> VideoFetchStopped: that download stops (its owner gave the file up).
  sources.torrent_file.drop TorrentFileDrop: the cached file goes (the cascade of Р-19).
Events (owner: sources; the payload is the model written as JSON):
  sources.table.progress TableProgress {url, phase, current, total, rate}   how far a read is
  sources.table.ready TableReady {url, read_at, fresh_until}                a complete table is stored and served, current until fresh_until
  sources.table.fresh TableReady                                           the term of the site of the page changed: the end of its term moved
  sources.table.failed TableFailed {url, reason}                           the read failed; the last complete table still serves
  sources.pages.forgotten PagesForgotten {urls}                            the tables of these pages are gone: no source lists them
  sources.settings.changed SourcesSettingsView                            the mirrors of a tracker were saved: every open settings screen shows
                                                                           them without asking back
  sources.table.trace TableTrace {url, site, kind: begin | end, ...}       what a read does, for its dump (modules/runlog): `end` carries the
                                                                           seconds of the read and what every player of the page gave (rows by
                                                                           state, frames, the causes of the rows without a video)
  sources.provider.changed ProviderView                                   the whole picture of the provider, told again whenever the socket, the
                                                                           manifest, the address or the pages of the sources change (`seq` grows
                                                                           with every one); the screen takes it as it is
  sources.provider.state ProviderState                                    how the socket of the provider stands (connecting | up | down |
                                                                           incompatible; absent when none is configured)
  sources.provider.manifest ManifestApplied {provider, revision}          the manifest of the provider was applied: its sites and players are
                                                                           those of this revision
  sources.video.progress {target, phase, progress, ...}                    how far a download of a player goes; the figures differ by player
Events in: series.source.added | .updated {changed has url} | .removed, series.deleted, system.started — the tables of pages belong to the sources
of the series: the module asks the catalog which pages its sources list (`catalog.series.list`) and forgets the table of a page that was listed
and is not any more at once, and of a page that never was (a preview, one left from before) when its table is past the term; a read of it that
goes is stopped first. `series.deleted` is answered with `series.purged {owner: sources}` (the cascade of Р-19).
settings.changed {key: sources.terms} — the terms of the sites: the tables of pages, the listings of VK channels and the addresses parsers of the
provider gave are current for the term of their site; every stored table of a site whose term moved is told again (`sources.table.fresh`).
settings.changed {key: voices.players_off} — the players the owner switched off for every source: a read asks none of them (ReadContext.off) and
keeps their rows of the last read as they were; a player switched on is asked at the next read of a page (the term, or a read the user asks for).
A failed read is not retried by itself and a consumer does not ask again on `failed`: the next request after a failure begins a new read. Only a
complete table is ever published. A stop in the middle of a read is no verdict: the state `reading` is stored and `on_start` begins the read again.

The reads (a table, a check of pages, a search of titles, a stream) are handled concurrently: each waits for the first look at the provider when a page
of its world is asked, and none takes an order from another, so a wait does not hold the queue of the topic.

Delivery goes ONLY through `delivery.Delivery` (the one way out): it walks over the candidate hosts (the canonical host of the link -> the
mirrors of the tracker -> the host as written) and checks that what was expected arrived. The transport is chosen by the tracker: kinozal /
rutracker through trackerauth (the sessions and the challenge are not our business); anilibria through its official API with httpx; astar /
anilibria_tv through the shared Playwright pool (_browser_html, a lazy start, a context per request).

Between the requests to one tracker a pause is kept (respect for another's service): a lock per service plus a minimum interval.
"""
from __future__ import annotations

import json
import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Callable, Iterable

from calc.sources.address import is_web_address
from calc.sources.players import Registered, players_list
from calc.sources.releases import page_release
from calc.sources.term import DEFAULT_MINUTES, TERMS_KEY, minutes_of, terms_of
from calc.sources.terms import terms_view
from calc.sources.views import Reader, sources_settings, stream_found
from core import ids
from core.module import BaseModule
from core import storage
from core.contracts.torrent import PageReleases
from core.contracts.voices import PLAYERS_OFF_KEY
from core.contracts import (BlockedPage, MirrorsSaved, MirrorsSet, NotAllowed, PageChanged, PageUrl, PagesCheck, PagesCheckReply,
                            PagesForgotten, PlayersListReply, PlayersListRequest, SeriesDeleted, SeriesPurged, SettingValue, SiteResolveReply,
                            SiteResolveRequest, SourceEvent, SourceType, SourcesSettingsView, StreamReply, TableCancel, TableCancelled,
                            TableGetReply, TableGetRequest, TableRead, TableRefresh, TablesFresh, TablesFreshReply, TermSaved, TermSet,
                            TermsReply, TitleSearchReply, TitleSearchRequest, TorrentFile, TorrentFileDrop, TorrentFileGet, TrackersReply,
                            VideoFetch, VideoFetched, VideoFetchStop, VideoFetchStopped, VideoProgress, VideoStream)
from core.topics import (CATALOG_SERIES_LIST, SERIES_DELETED, SERIES_PURGED, SERIES_SOURCE_ADDED, SERIES_SOURCE_REMOVED,
                         SERIES_SOURCE_UPDATED, SETTINGS_CHANGED, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SOURCES_PAGES_CHECK,
                         SOURCES_PAGES_FORGOTTEN, SOURCES_PARSE, SOURCES_PLAYERS_LIST, SOURCES_PROVIDER_GET, SOURCES_PROVIDER_SET,
                         SOURCES_RELEASES_GET, SOURCES_SETTINGS_CHANGED, SOURCES_SETTINGS_GET, SOURCES_SITE_RESOLVE,
                         SOURCES_TABLE_CANCEL, SOURCES_TABLE_FRESH, SOURCES_TABLE_GET, SOURCES_TABLE_READ, SOURCES_TABLE_REFRESH,
                         SOURCES_TABLES_FRESH, SOURCES_TERMS_GET, SOURCES_TERMS_SET, SOURCES_TITLES_SEARCH,
                         SOURCES_TORRENT_FILE_DROP, SOURCES_TORRENT_FILE_GET, SOURCES_TRACKER_SET_MIRRORS, SOURCES_TRACKERS_LIST,
                         SOURCES_VIDEO_FETCH, SOURCES_VIDEO_FETCH_STOP, SOURCES_VIDEO_PROGRESS, SOURCES_VIDEO_STREAM, SYSTEM_STARTED,
                         TABLE_READ_S, UNPACK_TABLE_CHANGED)
from core.db import Database
from core.envelope import Envelope
from core.http_limits import bounded_client
from core.logging import get_logger
from core.reasons import describe, reason

from . import anilibria, delivery, parsers
from .browser_pool import BrowserPool, chromium
from .delivery import Delivery
from .parsers import SourceParseError
from .provider import Connector, ProviderClient, ProviderHost, belongs_to_provider, not_connected
from .provider import block as provider_block
from .repository import SourcesRepository
from .term import Term
from .players.alloha_session import Profile as AllohaProfile
from .players.contract import StreamUnavailable, TransferRefused
from .players.registry import PlayerRegistry
from .players.streams import resolve_stream
from .sites import Site, SiteError, SiteRegistry
from .sites.builtin import builtin_sites
from .table import TableReader, utc_now

REQUEST_MIN_INTERVAL = 3.0  # seconds between requests to one tracker
VK_API_BASE = "https://api.vk.com/method/"


class SourcesModule(BaseModule):
    name = "sources"

    # The browser delivery of astar/anilibria_tv (Р-9). UA/viewport are as in the old firefox parser (the protection of the
    # trackers fingerprints the browser).
    _BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                   "Gecko/20100101 Firefox/125.0")
    BROWSER_TIMEOUT = 30000  # ms for the load / the JS challenge in the browser

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
            reply=lambda topic, payload: self.reply_of(topic, payload),
            site_of=lambda url: self.tables.resolve(url).site, default=provider, reconnect=provider_reconnect)
        self.tables = TableReader(
            self.repo, self.sites, lambda topic, payload: self.publish_event(topic, payload), lambda: self._http,
            players=self.players, clock=table_clock, safeguard_s=table_safeguard_s, recent=read_recent, term=self.term,
            holding=self.browsers.held, off=lambda: self.players_off, blocked=self._provider_block,
            ask=lambda topic, payload: self.request(topic, payload))
        super().__init__(bus)
        # Late binding on purpose: tests replace the transport, not the bus.
        self._delivery = Delivery(
            reply=lambda topic, payload: self.reply_of(topic, payload),
            http=lambda: self._http,
            browser_html=lambda service, url: self._browser_html(service, url),
            log=self.log)

    def register(self) -> None:
        self.handle(SOURCES_PARSE, self.on_parse)
        self.handle(SOURCES_RELEASES_GET, self.on_releases_get)
        self.handle(UNPACK_TABLE_CHANGED, self.on_unpack_table_changed)
        self.handle(SOURCES_TORRENT_FILE_GET, self.on_torrent_file)
        self.handle(SOURCES_TORRENT_FILE_DROP, self.on_torrent_file_drop)
        self.handle(SOURCES_TRACKERS_LIST, self.on_trackers_list)
        self.handle(SOURCES_TRACKER_SET_MIRRORS, self.on_set_mirrors)
        self.handle(SOURCES_VIDEO_STREAM, self.on_video_stream, concurrent=True)
        self.handle(SOURCES_VIDEO_FETCH, self.on_video_fetch, concurrent=True)
        self.handle(SOURCES_VIDEO_FETCH_STOP, self.on_video_fetch_stop)
        self.handle(SOURCES_TABLE_GET, self.on_table_get, concurrent=True)
        self.handle(SOURCES_TABLE_REFRESH, self.on_table_refresh, concurrent=True)
        self.handle(SOURCES_TABLE_READ, self.on_table_read, concurrent=True)
        self.handle(SOURCES_TABLE_CANCEL, self.on_table_cancel)
        self.handle(SOURCES_SITE_RESOLVE, self.on_site_resolve)
        self.handle(SOURCES_SETTINGS_GET, self.on_settings_get)
        self.handle(SOURCES_TITLES_SEARCH, self.on_titles_search, concurrent=True)
        self.handle(SOURCES_PAGES_CHECK, self.on_pages_check, concurrent=True)
        self.handle(SOURCES_PROVIDER_GET, self.on_provider_get)
        self.handle(SOURCES_PROVIDER_SET, self.on_provider_set)
        self.handle(SOURCES_PLAYERS_LIST, self.on_players_list)
        self.handle(SOURCES_TERMS_GET, self.on_terms_get)
        self.handle(SOURCES_TABLES_FRESH, self.on_tables_fresh)
        self.handle(SOURCES_TERMS_SET, self.on_terms_set)
        self.handle(SETTINGS_CHANGED, self.on_settings_changed)
        # The tables of pages belong to the sources of the series: a page no source lists any more is forgotten.
        for topic in (SYSTEM_STARTED, SERIES_SOURCE_ADDED, SERIES_SOURCE_UPDATED, SERIES_SOURCE_REMOVED):
            self.handle(topic, self.on_pages_listing)
        self.handle(SERIES_DELETED, self.on_series_deleted)

    async def on_pages_listing(self, env: Envelope) -> None:
        """The service started or a source came, went or changed its address: the pages the sources list are counted
        again and the table of every page that is no longer listed goes (an update that changes no address changes
        nothing here)."""
        if env.topic == SERIES_SOURCE_UPDATED.name and "url" not in SourceEvent.model_validate(env.payload).changed:
            return
        await self._forget_unlisted_pages()
        self.provider.announce()  # how many sources each application has changed

    async def on_series_deleted(self, env: Envelope) -> None:
        """Cascade of a deleted series (Р-19): the tables of the pages only it listed go, and the owner reports it."""
        series_id = SeriesDeleted.model_validate(env.payload).series_id
        if await self._forget_unlisted_pages():
            self.publish_event(SERIES_PURGED, SeriesPurged(series_id=series_id, owner=self.name).model_dump(mode="json"))

    async def _forget_unlisted_pages(self) -> bool:
        """Ask the catalog which pages its sources list and forget the stored pages that none does. False when the catalog
        could not be asked: nothing is forgotten then, and the next change counts again."""
        async with self._listing:
            try:
                series = (await self.reply_of(CATALOG_SERIES_LIST, {})).series
                addresses = [source.url for one in series for source in one.sources]
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
                self.publish_event(SOURCES_PAGES_FORGOTTEN, PagesForgotten(urls=tuple(sorted(gone))).model_dump(mode="json"))
            return True

    async def on_start(self) -> None:
        self._http = bounded_client(timeout=20.0, follow_redirects=True)
        try:
            stored = (await self.reply_of(SETTINGS_VALUE_GET, {"key": TERMS_KEY})).value
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves every site at the default term
            stored = None
        self.term.minutes = terms_of(stored)
        try:
            players_off = (await self.reply_of(SETTINGS_VALUE_GET, {"key": PLAYERS_OFF_KEY})).value
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

    async def on_terms_get(self, env: Envelope) -> TermsReply:
        """Every site some source of a series reads, with how many sources read it and its term; `provider` marks the sites of the
        provider of external sources (no web address), which the window lists apart."""
        series = (await self.reply_of(CATALOG_SERIES_LIST, {})).series
        addresses = [(source.url, source.source_type) for one in series for source in one.sources]
        return TermsReply(sites=terms_view(addresses, self.term.minutes))

    async def on_tables_fresh(self, env: Envelope) -> TablesFreshReply:
        """{urls} -> {pages: [{url, read_at, fresh_until}]}: when the tables of the pages were read and until when they are
        current (the window shows it on each source; the events ready and fresh carry it on)."""
        request = TablesFresh.model_validate(env.payload)
        return TablesFreshReply(pages=tuple(await self.tables.fresh_of([str(url) for url in request.urls])))

    async def on_terms_set(self, env: Envelope) -> TermSaved:
        command = TermSet.model_validate(env.payload)
        site, minutes = str(command.site or ""), minutes_of(command.minutes)
        if not site or minutes is None:
            return TermSaved(ok=False, reason=reason("sources_term_invalid", value=str(command.minutes)))
        await self.request(SETTINGS_VALUE_SET, {"key": TERMS_KEY,
                                                "value": json.dumps({**self.term.minutes, site: minutes}, sort_keys=True)})
        return TermSaved(ok=True, minutes=minutes)

    async def on_settings_changed(self, env: Envelope) -> None:
        setting = SettingValue.model_validate(env.payload)
        if setting.key == TERMS_KEY:
            before, self.term.minutes = self.term.minutes, terms_of(setting.value)
            for site in {site for site in before.keys() | self.term.minutes.keys()
                         if before.get(site, DEFAULT_MINUTES) != self.term.minutes.get(site, DEFAULT_MINUTES)}:
                for fresh in await self.tables.fresh_of_site(site):
                    self.publish_event(SOURCES_TABLE_FRESH, fresh.model_dump(mode="json"))
        elif setting.key == PLAYERS_OFF_KEY:
            self._set_players_off(setting.value)

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

    # --- gentle access to a tracker ------------------------------------------------

    async def _polite(self, service: str) -> None:
        since = time.monotonic() - self._last_request.get(service, -1e9)
        if since < REQUEST_MIN_INTERVAL:
            await asyncio.sleep(REQUEST_MIN_INTERVAL - since)
        self._last_request[service] = time.monotonic()

    # --- queries --------------------------------------------------------------------

    async def on_trackers_list(self, env: Envelope) -> TrackersReply:
        return TrackersReply(trackers=await self.repo.all_trackers())

    async def on_set_mirrors(self, env: Envelope) -> MirrorsSaved:
        """The mirrors of a tracker (the tab of the settings, Р-22)."""
        command = MirrorsSet.model_validate(env.payload)
        await self.repo.set_mirrors(command.tracker_id, command.mirrors)
        await self._configure_trackers()
        self.publish_event(SOURCES_SETTINGS_CHANGED, (await self._settings()).model_dump(mode="json"))
        return MirrorsSaved(ok=True)

    async def on_torrent_file_drop(self, env: Envelope) -> None:
        """The cascade of Р-19: the deletion of a series clears the cache of its .torrent files (a command from
        torrents — only it knows the torrent_id)."""
        path = self._cache_path(TorrentFileDrop.model_validate(env.payload).torrent_id)
        await storage.call(
            lambda: os.path.isfile(path) and os.remove(path), owns=path)

    async def _configure_trackers(self) -> None:
        """The sites of the trackers read the addresses of the mirrors the settings hold (table `trackers`)."""
        try:
            trackers = await self.repo.all_trackers()
        except Exception:  # noqa: BLE001 - the sites keep their own domains; the next change of the mirrors tries again
            self.log.exception("the mirrors of the trackers were not read")
            return
        self.sites.configure(tracker_mirrors={t.canonical_name: list(t.mirrors) for t in trackers})

    async def on_releases_get(self, env: Envelope) -> PageReleases:
        """The releases the page of a tracker lists now, read from the tracker (stage 15): `release` is the id of a release
        on the page, the same for every version of it."""
        url = PageUrl.model_validate(env.payload).url
        site = self.tables.resolve(url)
        parsed = await self._parse(url)
        return PageReleases(url=site.canonical_url or url, site=parsed["service"],
                            title=(parsed.get("title") or {}).get("ru") or "",
                            releases=tuple(page_release(parsed["service"], one) for one in parsed.get("releases") or []))

    async def on_unpack_table_changed(self, env: Envelope) -> None:
        """The tracks of a tracker's page changed (a release downloaded, read, updated, laid out anew): its table is made
        again from them; the end comes as `sources.table.ready` as for any page."""
        await self.tables.read(TableRead(url=PageChanged.model_validate(env.payload).url), changed=True)

    async def on_parse(self, env: Envelope) -> dict:
        return await self._parse(env.payload["url"])

    async def _parse(self, url: str) -> dict:
        tracker = await self.repo.resolve(url)
        if not tracker:
            raise SourceParseError(reason("tracker_unknown_url", url=url))
        service = tracker.canonical_name
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            await self._polite(service)
            result = await self._parse_by_service(service, url,
                                                  list(tracker.mirrors))
        # torrent_id is a constraint of the data (core/ids, Р-10/Р-22); it is counted here once, scan and the form of adding
        # use the ready one
        for r in result.get("releases", []):
            link_for_id = r.get("link") or r.get("magnet")
            if link_for_id and "torrent_id" not in r:
                r["torrent_id"] = ids.torrent_id(link_for_id,
                                                 r.get("date_marker"))
        result["service"] = service
        # What the module keeps of the tracker goes out as the immutable model; the features, a dictionary, as a copy of its own.
        result["ui_features"] = dict(tracker.ui_features)
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
        """The HTML of a tracker page through the browser. The preparation of the page depends on the tracker (a port of the
        old parsers): astar — open the list by a click on «Все торренты»; anilibria_tv — wait for the table after the JS
        challenge."""
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
        """A tracker page + its parsing. The parsing goes INSIDE the delivery attempt, so a page that could not be parsed
        means a move to the next host, not a refusal. The result carries the actual URL ('url')."""
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

    async def on_table_get(self, env: Envelope) -> TableGetReply:
        """The page as it stands, read from nothing; the table is handed over as it is, whatever its length."""
        request = TableGetRequest.model_validate(env.payload)
        await self._provider_known(request.url)
        return await self.tables.get(request)

    async def on_table_refresh(self, env: Envelope) -> TableGetReply:
        """Never waits for a read: the end of it comes as an event."""
        request = TableRefresh.model_validate(env.payload)
        await self._provider_known(request.url)
        return await self.tables.refresh(request)

    async def on_table_read(self, env: Envelope) -> None:
        request = TableRead.model_validate(env.payload)
        await self._provider_known(request.url)
        await self.tables.read(request)

    async def on_table_cancel(self, env: Envelope) -> TableCancelled:
        """The read of a page stopped by the user (a sync stopped): it ends failed, the last table serves."""
        return TableCancelled(cancelled=await self.tables.cancel(str(TableCancel.model_validate(env.payload).url)))

    async def on_settings_get(self, env: Envelope) -> SourcesSettingsView:
        return await self._settings()

    async def _settings(self) -> SourcesSettingsView:
        trackers = await self.repo.all_trackers()
        # A tracker is listed once, as a tracker, though its page is read by a site of the registry too.
        return sources_settings(trackers, [Reader(name=site.name, domains=tuple(site.domains),
                                                  of_tracker=site.source_type == SourceType.TORRENT.value) for site in self.sites])

    async def on_site_resolve(self, env: Envelope) -> SiteResolveReply:
        """No network."""
        return self.tables.resolve(SiteResolveRequest.model_validate(env.payload).url)

    async def on_players_list(self, env: Envelope) -> PlayersListReply:
        """Every player a row of a table can be of, with the words a window names it by and where it is among the pages
        asked about: the players of the registry, and those the manifest of the provider names (`line:<code>` of the
        sources of the provider), each in every place the manifest says it is."""
        request = PlayersListRequest.model_validate(env.payload or {})
        urls = list(dict.fromkeys(self.tables.resolve(url).canonical_url or url for url in request.urls))
        pages, heights = await self.repo.players_of_pages(urls)
        registered = [Registered(name=each.name, label=getattr(each, "label", "") or each.name,
                                 has_lines=bool(getattr(each, "lines", False))) for each in self.players]
        provided = tuple(self.connector.players()) if self.connector else ()
        labels = self.connector.labels() if self.connector else {}
        return PlayersListReply(players=players_list(registered, provided, pages, heights, labels))

    async def on_titles_search(self, env: Envelope) -> TitleSearchReply:
        """A page of the search of the titles of the provider of the socket, as the provider gives it, or the reason it gave
        none. No provider, no search: the user is told."""
        request = TitleSearchRequest.model_validate(env.payload)
        await self.provider.known()
        if (why := not_connected(self.connector)) is not None:
            raise NotAllowed(why)  # the search is the provider's: with none up there is nothing to search, and the user is told
        return await self.connector.search(request)

    @property
    def connector(self) -> Connector | None:
        return self.provider.connector

    async def on_provider_get(self, env: Envelope):
        return await self.provider.view()

    async def on_provider_set(self, env: Envelope):
        return await self.provider.save(env.payload)

    async def _provider_known(self, *urls: str) -> None:
        """A page of the provider's world is answered once the provider has been looked at: until its first hello or its first
        failure it is not down, it is not known (provider/gate.py). Any other page does not wait."""
        if any(belongs_to_provider(self.sites, url) for url in urls):
            await self.provider.known()

    def _provider_block(self, url: str) -> str | None:
        """The reason a page cannot be read now because the provider that reads it is not up; None for any other page."""
        return provider_block(self.connector, self.sites, url)

    async def on_pages_check(self, env: Envelope) -> PagesCheckReply:
        """The pages among them that cannot be read now for want of the provider that reads them (none configured, dropped,
        another protocol). A synchronization asks before it opens: a series with such a page is not synchronized, whatever its
        other pages are."""
        request = PagesCheck.model_validate(env.payload)
        await self._provider_known(*request.urls)
        return PagesCheckReply(blocked=tuple(BlockedPage(url=url, reason=why) for url in request.urls
                                             if (why := self._provider_block(url)) is not None))

    async def on_video_stream(self, env: Envelope) -> StreamReply:
        """The stream of a candidate link. `unasked`: the player was not reached (the provider is not up), so the error is
        nobody's failure. `error` means the URL belongs to a player that has no stream for it: the caller must not hand the
        page to a generic downloader."""
        request = VideoStream.model_validate(env.payload)
        url = request.url
        try:
            stream_info = await resolve_stream(self.players, url, self._http, request.max_height)
        except StreamUnavailable as exc:
            return StreamReply(handled=True, error=exc.reason, transient=exc.transient)
        if stream_info:
            return stream_found(stream_info)
        if not is_web_address(url):
            # A link of the provider's world no player owns: no generic downloader can open it. With the provider not up nobody
            # was asked (`unasked`: asked again later, never the source's failure); with it up the link is nobody's (`final`).
            await self.provider.known()
            if (why := not_connected(self.connector)) is not None:
                return StreamReply(handled=True, error=why, transient=True, unasked=True)
            return StreamReply(handled=True, error=reason("not_a_link_of_the_provider", url=url), transient=False)
        return StreamReply(handled=False)

    async def on_video_fetch(self, env: Envelope) -> VideoFetched:
        """The file of a link whose player downloads it itself (`player_fetch` of the stream): the player takes the file into
        `target`, telling how far it is by `sources.video.progress {target, ...figures}`. `kind` of a refusal is `final` (the
        source's last word), `broken` (a way that broke) or `unasked` (the source was not reached)."""
        request = VideoFetch.model_validate(env.payload)
        url, target = request.url, request.target
        player = next((each for each in self.players if each.owns(url) and hasattr(each, "transfer")), None)
        if player is None:
            return VideoFetched(ok=False, error=reason("no_player_downloads"), final=True, kind="final")

        async def told(figures: dict) -> None:
            # The figures are the player's own and their order is theirs: the event carries them as they came, once they are known to
            # keep to the standard of the telemetry (VideoProgress); a frame that breaks it is dropped, and the download goes on.
            payload = {**figures, "target": target}
            try:
                VideoProgress.model_validate(payload)
            except ValueError as exc:
                self.log.warning("%s: кадр хода загрузки не принят: %s", os.path.basename(target), exc)
                return
            self.publish_event(SOURCES_VIDEO_PROGRESS, {"target": target, **figures})

        work = asyncio.ensure_future(player.transfer(url, target, request.max_height, int(request.connections or 1), told))
        self._transfers[target] = work
        try:
            done = await work
        except asyncio.CancelledError:
            if work.cancelled():
                return VideoFetched(ok=False, error=reason("download_stopped"), final=False, kind="unasked")
            raise
        except TransferRefused as exc:
            self.log.warning("%s: загрузка плеером не удалась: %s", os.path.basename(target), describe(str(exc)))
            return VideoFetched(ok=False, error=str(exc), final=exc.final, kind=exc.kind)
        finally:
            self._transfers.pop(target, None)
        return VideoFetched(ok=True, **done)

    async def on_video_fetch_stop(self, env: Envelope) -> VideoFetchStopped:
        """The download of the player into that file stops (its owner gave the file up)."""
        work = self._transfers.get(VideoFetchStop.model_validate(env.payload).target)
        if work is not None:
            work.cancel()
        return VideoFetchStopped(stopped=work is not None)

    # --- the download of a .torrent (Р-1) ----------------------------------------------------

    async def on_torrent_file(self, env: Envelope) -> TorrentFile:
        request = TorrentFileGet.model_validate(env.payload)
        url, torrent_id = request.url, request.torrent_id
        cached = await storage.call(self._cache_read, torrent_id)
        if cached is not None:
            return TorrentFile(content_b64=base64.b64encode(cached).decode(), from_cache=True)

        tracker = await self.repo.resolve(url)
        service = tracker.canonical_name if tracker else ""
        mirrors = tracker.mirrors if tracker else []
        delivered = await self._delivery.fetch(
            service, url, expect="torrent", mirrors=mirrors,
            transport="auth" if service in ("kinozal", "rutracker") else "http",
            timeout=delivery.TORRENT_TIMEOUT)
        content = delivered.value

        await storage.call(self._cache_write, torrent_id, content, owns=self._cache_path(torrent_id))
        return TorrentFile(content_b64=base64.b64encode(content).decode(), from_cache=False)

    # --- the cache of the .torrent files ------------------------------------------------------

    def _cache_path(self, torrent_id: str) -> str:
        return os.path.join(self._cache_dir, f"{torrent_id}.torrent")

    def _cache_read(self, torrent_id: str) -> bytes | None:
        path = self._cache_path(torrent_id)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        return None

    def _cache_write(self, torrent_id: str, content: bytes) -> None:
        os.makedirs(self._cache_dir, exist_ok=True)
        with open(self._cache_path(torrent_id), "wb") as f:
            f.write(content)
