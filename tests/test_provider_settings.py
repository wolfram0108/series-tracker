"""The tab of the provider of external sources, from the core's side: the address and the token are settings taken at once, the
whole picture of the provider (the socket, the manifest, the applications with their sources and lines, the folder of the
downloads) is one view that is told again whenever a part of it changes, and a secret never goes round the bus."""
from __future__ import annotations

import asyncio

import pytest

from core import BaseModule, Bus, BusRequestError
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

    async def on_list(self, env) -> list[dict]:
        return [{"sources": [{"url": each} for each in self.addresses]}]

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
        return (await self.probe.request("settings.value.get", {"key": key}))["value"]


async def build(tmp_path, *, default: ProviderClient | None = None, addresses=("app://mock/7",)) -> World:
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
    mock.add_app(app, label)
    mock.card(app, 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "Y", "tok 2", [720])])
    return mock


async def test_with_no_address_the_picture_is_none_and_has_no_applications(tmp_path):
    world = await build(tmp_path)
    try:
        view = await world.view()
        assert (view["url"], view["state"], view["apps"], view["has_token"]) == ("", "none", [], False)
        assert view["downloads_dir"] == "/nas/work/downloads" and view["provider"] is None
    finally:
        await stop(world)


async def test_the_picture_of_a_provider_that_is_up_is_its_manifest_with_the_sources_and_lines_of_each_application(tmp_path):
    mock = mock_of("mock", "Мок (Mock)")
    async with serving(mock) as server:
        world = await build(tmp_path, default=ProviderClient(server.url, "secret"), addresses=("app://mock/7", "app://mock/8", "https://x.test/p"))
        try:
            view = await world.until(lambda each: each["state"] == "up" and each["apps"])
            assert view["url"] == server.url and view["has_token"] is True and view["protocol"] == 1
            assert view["provider"] == mock.manifest().provider.id and view["revision"] == mock.manifest().revision
            assert view["apps"] == [{"name": "mock", "label": "Мок (Mock)", "sources": 2, "lines": 2}]
            assert "secret" not in str(view)  # the token is told as present, never as its value
        finally:
            await stop(world)


async def test_an_address_saved_is_taken_at_once_and_the_sites_of_the_old_provider_leave_with_it(tmp_path):
    first, second = mock_of("one", "Первый (One)"), mock_of("two", "Второй (Two)")
    async with serving(first) as one, serving(second) as two:
        world = await build(tmp_path)
        try:
            saved = await world.save(one.url)
            assert saved["saved"] is True and saved["problems"] == []
            await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["apps"]] == ["one"])
            assert world.sources.tables.resolve("app://one/7").site == "one"
            await world.save(two.url)
            view = await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["apps"]] == ["two"])
            assert view["url"] == two.url
            assert world.sources.tables.resolve("app://one/7").site is None and world.sources.tables.resolve("app://two/7").site == "two"
            await world.save("")
            gone = await world.until(lambda each: each["state"] == "none")
            assert gone["apps"] == [] and world.sources.tables.resolve("app://two/7").site is None
            assert await world.stored("provider.url") == ""  # stored empty: the owner's «none», not the environment's address
        finally:
            await stop(world)


async def test_an_address_that_is_no_address_is_refused_with_its_reason_and_nothing_is_written(tmp_path):
    world = await build(tmp_path)
    try:
        refused = await world.save("not an address")
        assert refused["saved"] is False and [each["field"] for each in refused["problems"]] == ["url"]
        assert refused["problems"][0]["detail"]["code"] == "provider_url_invalid"
        assert await world.stored("provider.url") is None
    finally:
        await stop(world)


async def test_the_token_is_a_secret_kept_encrypted_told_by_its_presence_and_never_carried_by_an_event(tmp_path):
    mock = mock_of("mock", "Мок (Mock)")
    async with serving(mock) as server:
        world = await build(tmp_path)
        try:
            saved = await world.save(server.url, "s3cret")
            assert saved["provider"]["has_token"] is True and "s3cret" not in str(saved)
            assert await world.stored("provider.token") == "s3cret"
            rows = await world.db.fetch_all("SELECT value FROM settings WHERE key='provider.token'")
            assert rows and rows[0]["value"] != "s3cret"  # encrypted at rest
            told = []
            while not world.settings_events.queue.empty():
                told.append(world.settings_events.queue.get_nowait().payload)
            assert {"key": "provider.token", "value": ""} in told and "s3cret" not in str(told)
            assert (await world.save(server.url))["provider"]["has_token"] is True  # no token in the draft: the stored one stays
            assert await world.stored("provider.token") == "s3cret"
            assert (await world.save(server.url, ""))["provider"]["has_token"] is False  # an empty one clears it
            assert (await world.probe.request("settings.values.by_prefix", {"prefix": "provider."}))["values"] == {"provider.url": server.url}
        finally:
            await stop(world)


async def test_the_address_the_owner_saved_stands_over_the_environments_and_a_saved_empty_one_is_none(tmp_path):
    env, saved = mock_of("env", "Среда (Env)"), mock_of("saved", "Сохранён (Saved)")
    async with serving(env) as from_env, serving(saved) as from_owner:
        path = tmp_path / "keep"
        path.mkdir()
        world = await build(path, default=ProviderClient(from_env.url))
        try:
            await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["apps"]] == ["env"])
            await world.save(from_owner.url)
            await world.until(lambda each: each["state"] == "up" and [a["name"] for a in each["apps"]] == ["saved"])
        finally:
            await stop(world)
        again = await build(path, default=ProviderClient(from_env.url))  # a restart: what was saved is taken, not the environment's
        try:
            view = await again.until(lambda each: each["state"] == "up" and each["apps"])
            assert view["url"] == from_owner.url and [a["name"] for a in view["apps"]] == ["saved"]
            await again.save("")
        finally:
            await stop(again)
        third = await build(path, default=ProviderClient(from_env.url))
        try:
            assert (await third.view())["state"] == "none" and third.sources.connector is None
        finally:
            await stop(third)


async def test_of_two_pictures_the_one_taken_later_has_the_greater_number_and_shows_the_later_state_whichever_ends_first(tmp_path):
    """The window keeps the picture with the greatest number: a slow older picture that ends after a newer one must not number
    above it."""
    mock = mock_of("mock", "Мок (Mock)")
    async with serving(mock) as server:
        world = await build(tmp_path, default=ProviderClient(server.url), addresses=("app://mock/7",))
        try:
            await world.until(lambda each: each["state"] == "up" and each["apps"])
            host, gate, real = world.sources.provider, asyncio.Event(), world.sources.provider._sources_by_app
            calls = []

            async def slow_first():
                calls.append(1)
                if len(calls) == 1:
                    await gate.wait()
                return await real()

            host._sources_by_app = slow_first
            older = asyncio.ensure_future(host.view())           # taken now, with a token-less picture, and slow
            await asyncio.sleep(0)
            await host.save({"url": server.url, "token": "later"})  # the core changes under it
            newer = await host.view()
            gate.set()
            older = await older
            assert older.seq < newer.seq and older.has_token is False and newer.has_token is True
        finally:
            await stop(world)
