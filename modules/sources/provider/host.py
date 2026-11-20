"""The holder of the provider of the socket for the module `sources`: which provider the module talks to (the address and the token
the owner set, or the environment's until one is stored), the connector kept alive for it, and the whole picture of it the tab
of the provider shows.

An address saved is taken at once, without a restart: the feed of the old provider ends, its sites and players leave the
registries, the new one (or none) takes their place. The picture is told by the event `sources.provider.changed` whenever the
socket, the manifest, the address or the pages of the sources change, so that every open screen shows the same. What the picture
holds is counted in calc/sources/views.py; what a tab may write is calc/sources/provider_settings.py."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from calc.sources import provider_settings as settings
from calc.sources.terms import sources_by_site
from calc.sources.views import provider_view
from core.module import BusRequestError
from core.contracts import ProviderSaved, ProviderSettingsDraft, ProviderView
from core.logging import get_logger
from core.topics import (CATALOG_SERIES_LIST, CONFORM_WORKSPACE, SETTINGS_VALUE_GET, SETTINGS_VALUES_SET_MANY,
                         SOURCES_PROVIDER_CHANGED, Topic)

from ..players.registry import PlayerRegistry
from ..repository import SourcesRepository
from ..sites import SiteRegistry
from ..term import Term
from .client import ProviderClient
from .connector import Connector

Publish = Callable[[Topic, dict], None]
Request = Callable[..., Awaitable[dict]]
# The answer of a neighbour as the model of its topic (`BaseModule.reply_of`).
Reply = Callable[..., Awaitable[Any]]


class ProviderHost:
    def __init__(self, *, repo: SourcesRepository, sites: SiteRegistry, players: PlayerRegistry, term: Term, publish: Publish,
                 spawn: Callable[..., asyncio.Task], request: Request, reply: Reply, site_of: Callable[[str], str | None],
                 default: ProviderClient | None = None, reconnect: tuple[float, float] | None = None) -> None:
        self._repo, self._sites, self._players, self._term = repo, sites, players, term
        self._publish, self._spawn, self._request, self._reply, self._site_of = publish, spawn, request, reply, site_of
        self._reconnect = reconnect
        self._log = get_logger("sources")
        self._lock = asyncio.Lock()
        # Set when the start has settled which provider the module talks to, or that it talks to none (`known`).
        self._decided = asyncio.Event()
        self._feed: asyncio.Task | None = None
        self._seq = 0
        self.connector: Connector | None = self._connector_of(default) if default is not None else None

    # --- the connector ------------------------------------------------------------------------------------------------

    def _connector_of(self, client: ProviderClient) -> Connector:
        return Connector(client, self._repo, sites=self._sites, players=self._players, term=self._term, publish=self._told,
                         **({"reconnect": self._reconnect} if self._reconnect else {}))

    def _told(self, topic: Topic, payload: dict) -> None:
        """What the connector says goes out as it is, and the screen is told the whole picture again."""
        self._publish(topic, payload)
        self.announce()

    def announce(self) -> None:
        self._spawn(self._announce(), name="sources.provider.announce")

    async def _announce(self) -> None:
        self._publish(SOURCES_PROVIDER_CHANGED, (await self.view()).model_dump(mode="json"))

    # --- the life of it -----------------------------------------------------------------------------------------------

    async def _stored(self, key: str) -> str | None:
        try:
            return (await self._reply(SETTINGS_VALUE_GET, {"key": key})).value
        except Exception:  # noqa: BLE001 - a setting that cannot be read leaves what the environment named
            return None

    async def known(self) -> None:
        """The provider is looked at before anything is said of it: the module reads which provider it talks to at the start, and
        the feed makes its first attempt after that, so until the attempt ends the provider is not down, it is not known. Whoever
        asks about a page of the provider's world waits for the end of the attempt (its fuse is that of its topic)."""
        await self._decided.wait()
        while (connector := self.connector) is not None and not connector.looked.is_set():
            await connector.looked.wait()  # a provider put in its place is looked at in its turn

    async def start(self) -> None:
        """The provider the owner saved, else the environment's. The sites of its saved manifest first: a provider that is down
        at the start must not take the pages of its tables out of the hands of its sites."""
        try:
            await self._start()
        finally:
            self._decided.set()

    async def _start(self) -> None:
        url = await self._stored(settings.URL_KEY)
        if url is not None:
            token = await self._stored(settings.TOKEN_KEY) or ""
            if self.connector is not None and (self.connector.client.url, self.connector.client.token) != (url.rstrip("/"), token):
                await self.connector.close()  # the environment's was never started
                self.connector = None
            if self.connector is None:
                await self.use(url, token)
                return
        if self.connector is not None:
            await self.connector.load_saved()
            self._feed = self._spawn(self.connector.run(), name="sources.provider", lasting=True)

    async def stop(self) -> None:
        """The feed stops before its client closes: a loop that wakes to a closed client has nothing to read."""
        self._decided.set()  # nobody waits for a look that will not be made
        await self._stop_feed()
        if self.connector is not None:
            await self.connector.close()

    async def _stop_feed(self) -> None:
        if self._feed is not None:
            self._feed.cancel()
            await asyncio.gather(self._feed, return_exceptions=True)
            self._feed = None

    async def use(self, url: str, token: str) -> None:
        """Point the module at another provider, or at none (an empty address)."""
        async with self._lock:
            url = url.strip().rstrip("/")
            current = self.connector
            if current is None and not url:
                return
            if current is not None and (current.client.url, current.client.token) == (url, token) and self._feed is not None:
                return
            await self._stop_feed()
            if current is not None:
                current.release()
                await current.close()
            self.connector = None
            if url:
                self.connector = self._connector_of(ProviderClient(url, token))
                await self.connector.load_saved()
                self._feed = self._spawn(self.connector.run(), name="sources.provider", lasting=True)
            self._log.info("поставщик внешних источников: %s%s", url or "не задан", ", токен задан" if token else "")
            self.announce()

    # --- what the screen asks and sends ---------------------------------------------------------------------------------

    async def save(self, payload: dict) -> ProviderSaved:
        """The address and the token, checked as a whole, written and taken at once, or refused with coded problems."""
        draft = settings.clean(ProviderSettingsDraft.model_validate(payload))
        self._log.info("поставщик внешних источников: запись %s, токен %s", draft.url or "без адреса",
                       "не назван" if draft.token is None else "очищается" if draft.token == "" else "назван")
        problems = settings.check(draft)
        if not problems:
            if draft.token is None:
                draft = ProviderSettingsDraft(url=draft.url, token=(
                    self.connector.client.token if self.connector is not None else await self._stored(settings.TOKEN_KEY) or ""))
            await self._request(SETTINGS_VALUES_SET_MANY, {"values": settings.stored(draft)})
            await self.use(draft.url, draft.token or "")
        return ProviderSaved(saved=not problems, problems=problems, provider=await self.view())

    async def _sources_by_app(self) -> dict[str, int]:
        try:
            series = (await self._reply(CATALOG_SERIES_LIST, {})).series
        except (BusRequestError, asyncio.TimeoutError):
            return {}
        return sources_by_site((source.url for one in series for source in one.sources), self._site_of)

    async def _downloads_dir(self) -> str:
        try:
            return str((await self._request(CONFORM_WORKSPACE, {})).get("downloads") or "")
        except (BusRequestError, asyncio.TimeoutError):
            return ""

    async def view(self) -> ProviderView:
        """The picture as it stands now: its number and every part that can change under it are taken in one step, before anything is
        awaited, so that of two pictures the one taken later has the greater number and shows the later state — whichever is
        finished first."""
        self._seq += 1
        seq = self._seq
        connector = self.connector
        manifest = connector.manifest if connector is not None else None
        socket = dict(url=connector.client.url if connector is not None else "", has_token=bool(connector and connector.client.token),
                      state=connector.state if connector is not None else "none",
                      why=(connector.why or None) if connector is not None else None)
        # The catalog is asked only for a provider that has told its manifest: no manifest, no site to count sources for.
        sources = await self._sources_by_app() if manifest is not None else {}
        return provider_view(**socket, manifest=manifest, sources=sources, downloads_dir=await self._downloads_dir(), seq=seq)
