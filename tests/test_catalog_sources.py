"""Sources of a series in the catalog (stage 10, П-1): the queries and commands on its
sources, the events they raise, the refusals, and the cascade when a series is deleted.
"""
import sqlite3

import pytest

from core.bus import Bus
from core.module import BaseModule, BusRequestError
from core.runner import Runner
from core.db import Database
from core.reasons import code_of, parse, relayed
from modules.catalog import CatalogModule
from series_factory import create_series, source
from schema_db import migrated

VK = "https://vkvideo.ru/@chan|запрос"
RUCHIME = "https://1.ruchime.org/release/a"
DUNHUATV = "https://dunhuatv.ru/title/x"
ANIMAKIMA = "https://animakima.ru/pages/x/"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'один'), (2, 'два')")
        conn.commit()
    return str(path)


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [CatalogModule(bus, Database(db_path)), probe])
    await runner.start()
    yield bus, probe
    await runner.stop()


def code_of(exc: BusRequestError) -> str:
    """The reason code of a refusal that travelled over the bus as `ValueError: {json}`."""
    return parse(str(exc).split(": ", 1)[1])["code"]


def drain(sub) -> list:
    """What the subscription holds now; a handler publishes before it replies, so nothing is awaited."""
    found = []
    while not sub.queue.empty():
        env = sub.queue.get_nowait()
        found.append((env.topic, env.payload))
    return found


# --- creating a series ---------------------------------------------------------------

def data(**more):
    return {"name": "Т", "name_en": "T", "site": "kinozal", "save_path": "/media/t", **more}


# --- reading sources --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_caller_that_names_its_series_cannot_reach_a_source_of_another(system):
    _, probe = system
    mine = await create_series(probe, name="1", sources=[source(url=RUCHIME, source_type="web_video"),
                                                         source(url=VK, source_type="vk_video")])
    other = await create_series(probe, name="2", sources=[source(url=RUCHIME, source_type="web_video")])
    foreign = other.source_ids[0]
    with pytest.raises(BusRequestError, match="source_not_in_series"):
        await probe.request("catalog.sources.update", {"source_id": foreign, "series_id": mine.series_id,
                                                       "fields": {"vibix_season": 2}}, timeout=5)
    with pytest.raises(BusRequestError, match="source_not_in_series"):
        await probe.request("catalog.sources.remove", {"source_id": foreign, "series_id": mine.series_id}, timeout=5)
    assert len((await probe.request("catalog.sources.list", {"series_id": other.series_id}, timeout=5)).sources) == 1
    done = await probe.request("catalog.sources.update", {"source_id": mine.source_ids[0], "series_id": mine.series_id,
                                                          "fields": {"vibix_season": 2}}, timeout=5)
    assert done["applied"] == {"vibix_season": 2}
    assert (await probe.request("catalog.sources.remove", {"source_id": mine.source_ids[1],
                                                           "series_id": mine.series_id}, timeout=5))["ok"]


# --- adding a source ------------------------------------------------------------------------

# --- changing a source ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_refuses_what_a_source_cannot_hold(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video"),
                                               source(url=VK, source_type="vk_video")])
    sub = bus.subscribe("series.source.*")

    async def attempt(fields):
        with pytest.raises(BusRequestError) as refused:
            await probe.request("catalog.sources.update", {"source_id": made.source_ids[0], "fields": fields},
                                timeout=5)
        return refused.value

    assert code_of(await attempt({"url": VK})) == "source_exists"
    assert code_of(await attempt({"parser_profile_id": 77})) == "profile_not_found"
    assert code_of(str(await attempt({"series_id": 2}))) == "source_field_unknown"
    assert parse(relayed(str(await attempt({"id": 2}))))["params"]["field"] == "id"
    assert "source_type" in str(await attempt({"source_type": "ftp"}))
    assert "vibix_season" in str(await attempt({"vibix_season": 0}))
    assert drain(sub) == []
    with pytest.raises(BusRequestError, match="source_not_found"):
        await probe.request("catalog.sources.update", {"source_id": 999, "fields": {"position": 1}}, timeout=5)


# --- removing a source ------------------------------------------------------------------------------

# --- deleting a series ------------------------------------------------------------------------------------

# --- the colour of a source ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_source_takes_the_first_colour_its_series_has_free_and_keeps_it(system):
    _, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video"),
                                               source(url=RUCHIME, source_type="web_video")])
    other = await create_series(probe, name="2", sources=[source(url=RUCHIME, source_type="web_video")])
    third = await probe.request("catalog.sources.add", {
        "series_id": made.series_id, "source": source(url=DUNHUATV, source_type="web_video")}, timeout=5)

    def colours(series_id):
        return probe.request("catalog.sources.list", {"series_id": series_id}, timeout=5)

    assert [(each.id, each.color) for each in (await colours(made.series_id)).sources] == [
        (made.source_ids[0], 0), (made.source_ids[1], 1), (third["id"], 2)]
    assert [each.color for each in (await colours(other.series_id)).sources] == [0]  # colours are of a series
    await probe.request("catalog.sources.remove", {"source_id": made.source_ids[1]}, timeout=5)
    fourth = await probe.request("catalog.sources.add", {
        "series_id": made.series_id, "source": source(url=ANIMAKIMA, source_type="web_video")}, timeout=5)
    by_id = {each.id: each.color for each in (await colours(made.series_id)).sources}
    assert by_id == {made.source_ids[0]: 0, third["id"]: 2, fourth["id"]: 1}  # the freed colour, the others kept
    with pytest.raises(BusRequestError):
        await probe.request("catalog.sources.update", {"source_id": third["id"], "fields": {"color": 5}}, timeout=5)
