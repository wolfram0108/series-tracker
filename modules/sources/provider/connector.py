"""The tracker's end of the socket of a provider (contracts/provider_socket.md).

The connector keeps one thing true: the sites and players of the registries of the module are those of the provider's last
manifest. It reads the manifest when the feed of the provider says its revision is another (`hello`, `changed`), saves it in
the store of the module, and applies it; it registers the sites of the SAVED manifest before the provider is reached, so that a
provider that is down at the start takes no page of the tracker's tables out of its sites' hands.

The feed is the only way the connector learns of the provider: a dropped feed is the state `down`, and the feed is opened
again at a growing pace (the recovery of an external system that sends no notice of its return). Every state change is an
event of the module (`sources.provider.state`)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from typing import Callable, Iterable

from core.contracts import AppResults, AppSearchReply, AppSearchRequest
from core.contracts.provider import (EVENT_CHANGED, EVENT_HELLO, PROTOCOL, PROVIDER_INCOMPATIBLE, PROVIDER_UNAVAILABLE, Manifest,
                                     ManifestPlayer, ProviderState, ReadRequest, StreamReply, StreamRequest)
from core.reasons import reason

from ..players.registry import PlayerRegistry
from ..repository import SourcesRepository
from ..sites import SiteRegistry
from ..term import Term
from .client import ProviderClient, ProviderError
from .remote import RemoteFamily, RemoteSite

# The pace at which the feed is opened again after it dropped: it doubles to the ceiling and starts over once the feed is
# up (the recovery of an external system, spec §10).
RECONNECT_FROM_S = 1.0
RECONNECT_UP_TO_S = 30.0

Publish = Callable[[str, dict], None]
STATE_TOPIC = "sources.provider.state"
MANIFEST_TOPIC = "sources.provider.manifest"


class Connector:
    def __init__(self, client: ProviderClient, repo: SourcesRepository, *, sites: SiteRegistry, players: PlayerRegistry,
                 term: Term, publish: Publish, reconnect: tuple[float, float] = (RECONNECT_FROM_S, RECONNECT_UP_TO_S),
                 log: logging.Logger | None = None) -> None:
        self.client, self._repo, self._sites, self._players = client, repo, sites, players
        self._term, self._publish = term, publish
        self._reconnect = reconnect
        self._log = log or logging.getLogger("sources")
        self.manifest: Manifest | None = None
        self.state = "connecting"
        # What this connector put into the registries, so that a manifest that changes takes out exactly that.
        self._sites_ours: list[str] = []
        self._players_ours: list[str] = []
        self._wired = False

    # --- what the sites and the players of the provider ask of the socket ----------------------------------------------

    def read(self, request: ReadRequest):
        return self.client.read(request)

    async def stream(self, request: StreamRequest) -> StreamReply:
        return await self.client.stream(request)

    @property
    def term_hours(self) -> int:
        return self._term.hours

    def known_player(self, name: str) -> bool:
        return name in self._players

    # --- the state ---------------------------------------------------------------------------------------------------

    def _set(self, state: str, why: str = "") -> None:
        """The state of the socket changed: the event says it once for each change."""
        if state == self.state and not why:
            return
        changed = state != self.state
        self.state = state
        if changed:
            self._log.info("поставщик %s: %s%s", self.client.url, state, f" ({why})" if why else "")
            self._publish(STATE_TOPIC, ProviderState(provider=self.manifest.provider.id if self.manifest else self.client.url,
                                                     state=state, revision=self.manifest.revision if self.manifest else "",
                                                     reason=why).model_dump(mode="json"))

    # --- the manifest ------------------------------------------------------------------------------------------------

    async def load_saved(self) -> None:
        """The last manifest the store holds for this provider is in force until the provider speaks: the sites of the pages
        already read keep their readers while it is down."""
        saved = await self._repo.provider_manifest(self.client.url)
        if saved is not None and saved.protocol == PROTOCOL:
            self._apply(saved)

    def _apply(self, manifest: Manifest) -> None:
        """Make the registries those of the manifest: the sites and the families of the one before are taken out, the new ones
        put in. A site or a family whose name or domain a registered one holds already is skipped with its reason (the
        registries refuse it). No await in here: the registries change at one step."""
        for name in self._sites_ours:
            self._sites.unregister(name)
        for name in self._players_ours:
            self._players.unregister(name)
        self._sites_ours, self._players_ours = [], []
        self.manifest = manifest
        self._publish(MANIFEST_TOPIC, {"provider": manifest.provider.id, "revision": manifest.revision})
        for entry in manifest.sites:
            try:
                self._sites.register(RemoteSite(entry, self))
            except ValueError as exc:
                self._log.warning("сайт поставщика %s пропущен: %s", entry.name, exc)
            else:
                self._sites_ours.append(entry.name)
        for entry in manifest.families:
            try:
                self._players.register(RemoteFamily(entry, self))
            except ValueError as exc:
                self._log.warning("плеер поставщика %s пропущен: %s", entry.name, exc)
            else:
                self._players_ours.append(entry.name)

    async def _sync(self, revision: str) -> None:
        """The provider told the revision of its manifest: read it again when it is not the one in force."""
        if self.manifest is not None and self.manifest.revision == revision:
            self._set("up")
            return
        manifest = await self.client.manifest()
        if manifest.protocol != PROTOCOL:
            self._set("incompatible", reason(PROVIDER_INCOMPATIBLE, protocol=manifest.protocol, accepted=PROTOCOL))
            return
        saved = await self._repo.provider_manifest(self.client.url)
        self._apply(manifest)
        if saved is None or saved.revision != manifest.revision:
            await self._repo.provider_manifest_save(self.client.url, manifest)
        self._set("up")

    async def run(self) -> None:
        """Keep the feed of the provider open for the life of the module: every `hello` and `changed` is the revision to be
        in step with; a feed that dropped is `down`, and is opened again."""
        pause = self._reconnect[0]
        while True:
            try:
                async with aclosing(self.client.events()) as feed:
                    async for event, data in feed:
                        pause = self._reconnect[0]
                        if event in (EVENT_HELLO, EVENT_CHANGED):
                            await self._sync(str(data.get("revision") or ""))
                why = reason(PROVIDER_UNAVAILABLE, cause="the feed was closed by the provider")
            except ProviderError as exc:
                why = exc.reason
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad turn of the feed is a drop of it, not the end of the connector
                self._log.exception("поток событий поставщика прерван")
                why = reason(PROVIDER_UNAVAILABLE, cause=f"{type(exc).__name__}: {exc}"[:200])
            if self.state != "incompatible":
                self._set("down", why)
            await asyncio.sleep(pause)
            pause = min(pause * 2, self._reconnect[1])

    async def close(self) -> None:
        await self.client.close()

    # --- what the provider adds to the answers of the module ---------------------------------------------------------

    async def search(self, request: AppSearchRequest) -> list[AppResults]:
        """The search of the provider, as the results of the applications it names; the reason of a failure, for each of its
        sources of search the request asks, when it cannot be reached."""
        if self.manifest is None:
            return []
        declared = [each for each in self.manifest.search if not request.app or each.name == request.app]
        if not declared:
            return []
        try:
            reply: AppSearchReply = await self.client.search(request)
        except ProviderError as exc:
            return [AppResults(app=each.name, label=each.label or each.name, page=request.page, reason=exc.reason)
                    for each in declared]
        return reply.apps

    def players(self) -> Iterable[ManifestPlayer]:
        """The players of the order of sources the provider names."""
        return self.manifest.players if self.manifest is not None else ()

    def labels(self) -> dict[str, str]:
        """The label of each site of the provider, by its name."""
        return {each.name: each.label or each.name for each in self.manifest.sites} if self.manifest is not None else {}
