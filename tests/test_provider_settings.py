"""The tab of the provider of external sources, from the core's side: the address and the token are settings taken at once, the
whole picture of the provider (the socket, the manifest, the sites with their sources and lines, the folder of the
downloads) is one view that is told again whenever a part of it changes, and a secret never goes round the bus."""
from __future__ import annotations

import asyncio


from core.bus import Bus
from core.contracts import SeriesListReply
from core.module import BaseModule
from series_factory import series_card
from core.db import Database
from modules.settings import SettingsModule
from modules.sources.module import SourcesModule
from modules.sources.provider import ProviderClient
from provider_mock import MockProvider, serving
from schema_db import migrated

WAIT_S = 10.0


class Probe(BaseModule):
    name = "probe"


class Neighbours(BaseModule):
    """What `sources` asks the others for the picture: the sources of the series and the folder of the downloads."""

    name = "neighbours"

    def __init__(self, bus, addresses: list[str]) -> None:
        self.addresses = addresses
        super().__init__(bus)

    def register(self) -> None:
        self.handle("catalog.series.list", self.on_list)
        self.handle("conform.workspace", self.on_workspace)

    async def on_list(self, env) -> SeriesListReply:
        return SeriesListReply(series=(series_card(sources=[{"url": each} for each in self.addresses]),))

    async def on_workspace(self, env) -> dict:
        return {"downloads": "/nas/work/downloads", "conform": "/nas/work/conform"}


class World:
    def __init__(self, bus, db, sources, probe, neighbours) -> None:
        self.bus, self.db, self.sources, self.probe, self.neighbours = bus, db, sources, probe, neighbours
        self.seen = bus.subscribe("sources.provider.changed")
        self.settings_events = bus.subscribe("settings.changed")

    async def until(self, wanted) -> dict:
        """The next picture the core tells that satisfies `wanted`."""
        while True:
            got = (await asyncio.wait_for(self.seen.queue.get(), WAIT_S)).payload
            if wanted(got):
                return got

    async def view(self) -> dict:
        return await self.probe.request("sources.provider.get", {})

    async def save(self, url: str, token: str | None = None) -> dict:
        return await self.probe.request("sources.provider.set", {"url": url, **({} if token is None else {"token": token})})

    async def stored(self, key: str):
        return (await self.probe.request("settings.value.get", {"key": key})).value


async def build(tmp_path, *, default: ProviderClient | None = None, addresses=("title://mock/7/X",)) -> World:
    kept = tmp_path / "p.db"  # a world built again in the same folder is the same database: a restart
    db, bus = Database(str(kept if kept.exists() else migrated(kept))), Bus()
    settings = SettingsModule(bus, db)
    sources = SourcesModule(bus, db, torrent_cache_dir=str(tmp_path / "cache"), provider=default, provider_reconnect=(0.05, 0.2))
    neighbours, probe = Neighbours(bus, list(addresses)), Probe(bus)
    world = World(bus, db, sources, probe, neighbours)
    for each in (settings, neighbours, probe, sources):
        await each.start()
    world.modules = (sources, probe, neighbours, settings)
    return world


async def stop(world: World) -> None:
    for each in world.modules:
        await each.stop()


def mock_of(app: str, label: str) -> MockProvider:
    mock = MockProvider()
    mock.add_site(app, label)
    mock.title(app, 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "Y", "tok 2", [720])])
    return mock


async def test_the_address_the_owner_saved_stands_over_the_environments_and_a_saved_empty_one_is_none(tmp_path):
    env, saved = mock_of("env", "Среда (Env)"), mock_of("saved", "Сохранён (Saved)")
    async with serving(env) as from_env, serving(saved) as from_owner:
        path = tmp_path / "keep"
        path.mkdir()
        world = await build(path, default=ProviderClient(from_env.url))
        try:
            await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["sites"]] == ["env"])
            await world.save(from_owner.url)
            await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["sites"]] == ["saved"])
        finally:
            await stop(world)
        again = await build(path, default=ProviderClient(from_env.url))  # a restart: what was saved is taken, not the environment's
        try:
            view = await again.until(lambda each: each["state"] == "up" and each["sites"])
            assert view["url"] == from_owner.url and [a["name"] for a in view["sites"]] == ["saved"]
            await again.save("")
        finally:
            await stop(again)
        third = await build(path, default=ProviderClient(from_env.url))
        try:
            assert (await third.view())["state"] == "none" and third.sources.connector is None
        finally:
            await stop(third)
