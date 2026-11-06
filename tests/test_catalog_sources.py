"""Sources of a series in the catalog (stage 10, П-1): the kind of a series, the queries and commands on its
sources, the events they raise, the refusals, and the cascade when a series is deleted.
"""
import asyncio
import sqlite3

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.contracts import SeriesSource, SourceEvent
from core.db import Database
from core.reasons import parse
from modules.catalog import CatalogModule
from modules.catalog.repository import CatalogRepository
from modules.catalog.series_sources import REMOVED_FIELDS, kind_of
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


# --- the kind of a series -------------------------------------------------------

@pytest.mark.parametrize("types, expected", [
    (["torrent"], "torrent"),
    (["vk_video"], "video"),
    (["web_video"], "video"),
    (["web_video", "vk_video", "web_video"], "video"),
    (["web_video", "torrent"], "torrent"),
    ([], "video"),
])
def test_kind_is_torrent_while_any_source_is_a_torrent(types, expected):
    assert kind_of([{"source_type": t} for t in types]) == expected


@pytest.mark.asyncio
async def test_a_series_is_given_with_its_kind_and_its_sources(system):
    _, probe = system
    torrent = await create_series(probe, name="Т", sources=[source()])
    video = await create_series(probe, name="В", sources=[
        source(url=RUCHIME, source_type="web_video", parser_profile_id=2, vibix_season=2, players_off=["vk"]),
        source(url=VK, source_type="vk_video", parser_profile_id=1)])

    one = await probe.request("catalog.series.get", {"series_id": torrent.series_id}, timeout=5)
    two = await probe.request("catalog.series.get", {"series_id": video.series_id}, timeout=5)
    listed = {row["id"]: row for row in await probe.request("catalog.series.list", {}, timeout=5)}

    assert (one["kind"], two["kind"]) == ("torrent", "video")
    assert listed[video.series_id] == two
    assert [s["id"] for s in two["sources"]] == list(video.source_ids)
    assert two["sources"][0] == {"id": video.source_ids[0], "series_id": video.series_id, "position": 0,
                                 "url": RUCHIME, "source_type": "web_video", "parser_profile_id": 2,
                                 "vibix_season": 2, "players_off": ["vk"], "color": 0}
    assert two["sources"][1]["url"] == VK
    for each in (*one["sources"], *two["sources"]):
        assert list(each) == list(SeriesSource.model_fields)
        SeriesSource.model_validate(each)


@pytest.mark.asyncio
async def test_the_columns_that_left_the_series_are_not_in_its_row(system):
    _, probe = system
    made = await create_series(probe)
    row = await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5)
    for name in REMOVED_FIELDS:
        assert name not in row


# --- creating a series ---------------------------------------------------------------

def data(**more):
    return {"name": "Т", "name_en": "T", "site": "kinozal", "save_path": "/media/t", **more}


@pytest.mark.asyncio
@pytest.mark.parametrize("sources", [None, []])
async def test_a_series_without_a_source_is_refused(system, sources):
    _, probe = system
    given = data() if sources is None else data(sources=sources)
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.series.create", {"data": given}, timeout=5)
    assert code_of(refused.value) == "no_source"
    assert await probe.request("catalog.series.list", {}, timeout=5) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", REMOVED_FIELDS)
async def test_create_names_a_removed_field_instead_of_ignoring_it(system, name):
    _, probe = system
    given = data(sources=[source()], **{name: "x"})
    with pytest.raises(BusRequestError, match=f"'{name}'"):
        await probe.request("catalog.series.create", {"data": given}, timeout=5)
    assert await probe.request("catalog.series.list", {}, timeout=5) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [*REMOVED_FIELDS, "sources"])
async def test_update_names_a_removed_field_instead_of_ignoring_it(system, name):
    _, probe = system
    made = await create_series(probe)
    with pytest.raises(BusRequestError, match=f"'{name}'"):
        await probe.request("catalog.series.update", {
            "series_id": made.series_id, "fields": {"season": "2", name: "x"}}, timeout=5)
    # a refused update changes nothing, not even the fields that were fine
    row = await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5)
    assert row["season"] is None


@pytest.mark.asyncio
async def test_a_field_of_the_series_is_still_updated(system):
    _, probe = system
    made = await create_series(probe)
    reply = await probe.request("catalog.series.update", {
        "series_id": made.series_id, "fields": {"season": "3", "ignored_seasons": "[1]"}}, timeout=5)
    assert reply == {"applied": {"season": "3", "ignored_seasons": "[1]"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad, match", [
    ({"url": RUCHIME}, "source_type"),
    ({"source_type": "web_video"}, "url"),
    ({"url": RUCHIME, "source_type": "ftp"}, "source_type"),
    ({"url": "  ", "source_type": "web_video"}, "url"),
    ({"url": RUCHIME, "source_type": "web_video", "vibix_season": 0}, "vibix_season"),
    ({"url": RUCHIME, "source_type": "web_video", "series_id": 5}, "series_id"),
    ({"url": RUCHIME, "source_type": "web_video", "player": "vk"}, "player"),
])
async def test_a_source_that_breaks_the_contract_is_refused_and_nothing_is_made(system, bad, match):
    _, probe = system
    with pytest.raises(BusRequestError, match=match):
        await probe.request("catalog.series.create", {"data": data(sources=[bad])}, timeout=5)
    assert await probe.request("catalog.series.list", {}, timeout=5) == []


@pytest.mark.asyncio
async def test_the_same_address_twice_in_one_series_is_refused(system):
    _, probe = system
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.series.create", {"data": data(sources=[
            source(url=VK, source_type="vk_video"), source(url=VK, source_type="vk_video")])}, timeout=5)
    assert code_of(refused.value) == "source_exists"
    assert await probe.request("catalog.series.list", {}, timeout=5) == []


@pytest.mark.asyncio
async def test_an_unknown_profile_leaves_no_series_behind(system):
    _, probe = system
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.series.create", {"data": data(sources=[
            source(url=VK, source_type="vk_video", parser_profile_id=1),
            source(url=RUCHIME, source_type="web_video", parser_profile_id=77)])}, timeout=5)
    assert code_of(refused.value) == "profile_not_found"
    assert parse(str(refused.value).split(": ", 1)[1])["params"] == {"profile_id": 77}
    assert await probe.request("catalog.series.list", {}, timeout=5) == []
    assert await probe.request("catalog.sources.by_profile", {"profile_id": 1}, timeout=5) == []


@pytest.mark.asyncio
async def test_creating_a_series_tells_of_it_and_then_of_each_source(system):
    bus, probe = system
    sub = bus.subscribe("series.#")
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video"),
                                               source(url=RUCHIME, source_type="web_video")])
    events = drain(sub)
    assert [topic for topic, _ in events] == ["series.added", "series.source.added", "series.source.added"]
    assert events[0][1]["id"] == made.series_id
    assert [e[1] for e in events[1:]] == [SourceEvent(series_id=made.series_id, source_id=i).model_dump()
                                          for i in made.source_ids]
    # the event carries the series whole, sources and kind included
    assert [s["url"] for s in events[0][1]["sources"]] == [VK, RUCHIME]
    assert events[0][1]["kind"] == "video"


# --- reading sources --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sources_are_listed_by_position_then_id(system):
    _, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video"),
                                               source(url=RUCHIME, source_type="web_video")])
    added = await probe.request("catalog.sources.add", {
        "series_id": made.series_id, "source": source(url=DUNHUATV, source_type="web_video", position=-5)}, timeout=5)
    listed = await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5)
    assert [s["id"] for s in listed] == [added["id"], *made.source_ids]
    assert [s["position"] for s in listed] == [-5, 0, 1]


@pytest.mark.asyncio
async def test_sources_are_found_by_the_profile_that_reads_them(system):
    _, probe = system
    first = await create_series(probe, name="1", sources=[source(url=RUCHIME, source_type="web_video",
                                                                  parser_profile_id=1)])
    await create_series(probe, name="2", sources=[source(url=VK, source_type="vk_video", parser_profile_id=2)])
    found = await probe.request("catalog.sources.by_profile", {"profile_id": 1}, timeout=5)
    assert [(s["series_id"], s["url"]) for s in found] == [(first.series_id, RUCHIME)]
    assert await probe.request("catalog.sources.by_profile", {"profile_id": 99}, timeout=5) == []


@pytest.mark.asyncio
async def test_a_torrent_and_a_video_source_do_not_meet_in_one_series(system):
    _, probe = system
    torrent = source(url="https://kinozal.tv/details.php?id=1", source_type="torrent")
    video = source(url=RUCHIME, source_type="web_video")
    with pytest.raises(BusRequestError) as at_creation:
        await probe.request("catalog.series.create", {"data": data(sources=[torrent, video])}, timeout=5)
    assert code_of(at_creation.value) == "mixed_kinds"
    assert await probe.request("catalog.series.list", {}, timeout=5) == []

    made = await create_series(probe, sources=[torrent])
    with pytest.raises(BusRequestError) as at_adding:
        await probe.request("catalog.sources.add", {"series_id": made.series_id, "source": video}, timeout=5)
    assert code_of(at_adding.value) == "mixed_kinds"
    assert [each["source_type"] for each in await probe.request(
        "catalog.sources.list", {"series_id": made.series_id}, timeout=5)] == ["torrent"]


@pytest.mark.asyncio
async def test_the_type_of_a_source_cannot_make_its_series_mixed(system):
    _, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video"),
                                               source(url=RUCHIME, source_type="web_video")])
    first, second = made.source_ids
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.sources.update", {"source_id": second, "fields": {"source_type": "torrent"}},
                            timeout=5)
    assert code_of(refused.value) == "mixed_kinds"
    changed = await probe.request("catalog.sources.update", {"source_id": first, "fields": {"source_type": "web_video"}},
                                  timeout=5)
    assert changed["applied"] == {"source_type": "web_video"}


@pytest.mark.asyncio
async def test_a_caller_that_names_its_series_cannot_reach_a_source_of_another(system):
    _, probe = system
    mine = await create_series(probe, name="1", sources=[source(url=RUCHIME, source_type="web_video"),
                                                         source(url=VK, source_type="vk_video")])
    other = await create_series(probe, name="2", sources=[source(url=RUCHIME, source_type="web_video")])
    foreign = other.source_ids[0]
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.update", {"source_id": foreign, "series_id": mine.series_id,
                                                       "fields": {"vibix_season": 2}}, timeout=5)
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.remove", {"source_id": foreign, "series_id": mine.series_id}, timeout=5)
    assert len(await probe.request("catalog.sources.list", {"series_id": other.series_id}, timeout=5)) == 1
    done = await probe.request("catalog.sources.update", {"source_id": mine.source_ids[0], "series_id": mine.series_id,
                                                          "fields": {"vibix_season": 2}}, timeout=5)
    assert done["applied"] == {"vibix_season": 2}
    assert (await probe.request("catalog.sources.remove", {"source_id": mine.source_ids[1],
                                                           "series_id": mine.series_id}, timeout=5))["ok"]


@pytest.mark.asyncio
async def test_the_sources_of_a_missing_series_are_an_error_not_an_empty_list(system):
    _, probe = system
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.list", {"series_id": 99}, timeout=5)


# --- adding a source ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_source_is_added_at_the_end_and_announced(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video")])
    sub = bus.subscribe("series.source.*")
    reply = await probe.request("catalog.sources.add", {
        "series_id": made.series_id,
        "source": {"url": RUCHIME, "source_type": "web_video", "parser_profile_id": 2, "players_off": ["vk", "vk"]}},
        timeout=5)
    assert set(reply) == {"id"}
    assert drain(sub) == [("series.source.added", SourceEvent(series_id=made.series_id,
                                                              source_id=reply["id"]).model_dump())]
    added = (await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5))[-1]
    assert added == {"id": reply["id"], "series_id": made.series_id, "position": 1, "url": RUCHIME,
                     "source_type": "web_video", "parser_profile_id": 2,
                     "vibix_season": None, "players_off": ["vk"], "color": 1}


@pytest.mark.asyncio
async def test_adding_the_same_address_twice_is_refused_and_silent(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video")])
    sub = bus.subscribe("series.source.*")
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.sources.add", {
            "series_id": made.series_id, "source": source(url=VK, source_type="vk_video")}, timeout=5)
    assert code_of(refused.value) == "source_exists"
    assert drain(sub) == []
    assert len(await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5)) == 1


@pytest.mark.asyncio
async def test_the_same_address_may_stand_in_two_series(system):
    _, probe = system
    first = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video")])
    second = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video")])
    assert first.source_id != second.source_id


@pytest.mark.asyncio
async def test_a_source_of_a_missing_series_or_a_missing_profile_is_refused(system):
    _, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video")])
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.add", {"series_id": 99, "source": source()}, timeout=5)
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.sources.add", {
            "series_id": made.series_id, "source": source(url=VK, source_type="vk_video", parser_profile_id=77)},
            timeout=5)
    assert code_of(refused.value) == "profile_not_found"


# --- changing a source ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_applies_what_changed_and_announces_it_once(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video", parser_profile_id=1)])
    sub = bus.subscribe("series.source.*")
    reply = await probe.request("catalog.sources.update", {
        "source_id": made.source_id,
        "fields": {"parser_profile_id": 2, "vibix_season": 3, "players_off": ["vibix"]}},
        timeout=5)
    # the name of the voice-over was already empty: it is not an applied change
    assert reply == {"applied": {"parser_profile_id": 2, "vibix_season": 3, "players_off": ["vibix"]}}
    assert drain(sub) == [("series.source.updated", SourceEvent(series_id=made.series_id,
                                                                source_id=made.source_id).model_dump())]
    stored = (await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5))[0]
    assert (stored["parser_profile_id"], stored["vibix_season"], stored["players_off"]) == (2, 3, ["vibix"])


@pytest.mark.asyncio
async def test_update_that_changes_nothing_says_nothing(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video", parser_profile_id=1)])
    sub = bus.subscribe("series.source.*")
    reply = await probe.request("catalog.sources.update", {
        "source_id": made.source_id, "fields": {"parser_profile_id": 1, "url": RUCHIME}}, timeout=5)
    assert reply == {"applied": {}}
    assert drain(sub) == []


@pytest.mark.asyncio
async def test_the_name_of_a_voice_over_is_no_field_of_a_source_and_a_caller_that_sends_it_is_refused(system):
    """Voice-overs are named by the rules and the groups; the name a source once had (0033) is gone, and a caller that
    still sends it is told so instead of being ignored."""
    bus, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video", parser_profile_id=1)])
    with pytest.raises(BusRequestError, match="voice_name"):
        await probe.request("catalog.sources.update", {"source_id": made.source_id, "fields": {"voice_name": "AniStar"}},
                            timeout=5)
    assert "voice_name" not in (await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5))[0]


@pytest.mark.asyncio
async def test_a_profile_can_be_taken_off_a_source(system):
    _, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME, source_type="web_video", parser_profile_id=1)])
    reply = await probe.request("catalog.sources.update", {
        "source_id": made.source_id, "fields": {"parser_profile_id": None}}, timeout=5)
    assert reply == {"applied": {"parser_profile_id": None}}


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
    assert "'series_id'" in str(await attempt({"series_id": 2}))
    assert "'id'" in str(await attempt({"id": 2}))
    assert "source_type" in str(await attempt({"source_type": "ftp"}))
    assert "vibix_season" in str(await attempt({"vibix_season": 0}))
    assert drain(sub) == []
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.update", {"source_id": 999, "fields": {"position": 1}}, timeout=5)


@pytest.mark.asyncio
async def test_changing_the_type_of_the_only_torrent_changes_the_kind(system):
    _, probe = system
    made = await create_series(probe)
    await probe.request("catalog.sources.update", {
        "source_id": made.source_id, "fields": {"source_type": "web_video"}}, timeout=5)
    row = await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5)
    assert row["kind"] == "video"


# --- removing a source ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_only_source_of_a_series_cannot_be_removed(system):
    bus, probe = system
    made = await create_series(probe)
    sub = bus.subscribe("series.source.*")
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.sources.remove", {"source_id": made.source_id}, timeout=5)
    assert code_of(refused.value) == "last_source"
    assert drain(sub) == []
    assert len(await probe.request("catalog.sources.list", {"series_id": made.series_id}, timeout=5)) == 1


@pytest.mark.asyncio
async def test_a_source_is_removed_and_announced_until_one_is_left(system):
    bus, probe = system
    made = await create_series(probe, sources=[source(url=VK, source_type="vk_video"),
                                               source(url=RUCHIME, source_type="web_video"),
                                               source(url=DUNHUATV, source_type="web_video")])
    sub = bus.subscribe("series.source.*")
    first, second, third = made.source_ids

    assert await probe.request("catalog.sources.remove", {"source_id": second}, timeout=5) == {"ok": True}
    assert await probe.request("catalog.sources.remove", {"source_id": first}, timeout=5) == {"ok": True}
    assert drain(sub) == [("series.source.removed", SourceEvent(series_id=made.series_id, source_id=i).model_dump())
                          for i in (second, first)]
    with pytest.raises(BusRequestError) as refused:
        await probe.request("catalog.sources.remove", {"source_id": third}, timeout=5)
    assert code_of(refused.value) == "last_source"
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.sources.remove", {"source_id": second}, timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("types, kind", [(["torrent"], "torrent"), (["vk_video", "web_video"], "video")])
async def test_the_kind_of_a_series_is_the_kind_of_its_sources(system, types, kind):
    _, probe = system
    made = await create_series(probe, sources=[source(url=f"https://example.org/{i}", source_type=each)
                                               for i, each in enumerate(types)])
    assert (await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5))["kind"] == kind


@pytest.mark.asyncio
async def test_two_removals_at_once_never_empty_a_series(db_path):
    """The check and the removal are one statement: of two removals racing for the last two sources, one loses."""
    db = Database(db_path)
    repo = CatalogRepository(db)
    for _ in range(5):
        made = await create_series(db, sources=[source(url=VK, source_type="vk_video"),
                                                source(url=RUCHIME, source_type="web_video")])
        outcomes = await asyncio.gather(*(repo.remove_source(i) for i in made.source_ids))
        assert sorted(outcomes) == [False, True]
        assert len(await repo.list_sources(made.series_id)) == 1


# --- deleting a series ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleting_a_series_takes_its_sources_and_only_its_own(system, db_path):
    bus, probe = system
    gone = await create_series(probe, name="уходит", sources=[source(url=VK, source_type="vk_video"),
                                                              source(url=RUCHIME, source_type="web_video")])
    stays = await create_series(probe, name="остаётся", sources=[source(url=RUCHIME, source_type="web_video")])
    sub = bus.subscribe("series.#")

    await probe.request("catalog.series.delete", {"series_id": gone.series_id}, timeout=5)

    db = Database(db_path)
    left = await db.fetch_all("SELECT id, series_id FROM series_sources")
    assert left == [{"id": stays.source_id, "series_id": stays.series_id}]
    assert [row["id"] for row in await db.fetch_all("SELECT id FROM series")] == [stays.series_id]
    # the series is announced gone once; its sources do not each announce theirs
    assert [topic for topic, _ in drain(sub)] == ["series.deleted"]


@pytest.mark.asyncio
async def test_a_series_with_children_of_other_modules_is_still_deleted_with_its_sources(db_path):
    """The owners of the other tables clean theirs on series.deleted; the sources are the catalog's own."""
    db = Database(db_path)
    made = await create_series(db)
    await db.execute("INSERT INTO scan_tasks (series_id, created_at, status, task_data, results_data) "
                     "VALUES (?, '2026-01-01', 'processing', '[]', '{}')", (made.series_id,))
    await CatalogRepository(db).delete_series(made.series_id)
    assert await db.fetch_all("SELECT id FROM series_sources") == []
    assert await db.fetch_all("SELECT id FROM series") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_by", ["sync", "library", None])
async def test_the_card_and_the_sources_of_a_busy_series_are_not_changed(system, busy_by):
    """A series is busy from the start of its sync until every work of the run has ended (ЭТАП_11): its card and its
    sources are refused with the reason of the table of what is allowed; a free series takes them."""
    _, probe = system
    made = await create_series(probe, sources=[source(url=RUCHIME)])
    if busy_by:
        probe.publish_event("series.busy.contribution", {"source": busy_by, "series_id": made.series_id, "busy": True})
        await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5)  # the event was heard
    [own] = (await probe.request("catalog.series.get", {"series_id": made.series_id}, timeout=5))["sources"]
    calls = [
        ("catalog.series.update", {"series_id": made.series_id, "fields": {"season": "2"}}),
        ("catalog.sources.add", {"series_id": made.series_id, "source": source(url=RUCHIME + "-2")}),
        ("catalog.sources.update", {"series_id": made.series_id, "source_id": own["id"], "fields": {"players_off": ["vk"]}}),
        ("catalog.sources.remove", {"series_id": made.series_id, "source_id": own["id"]}),
    ]
    for topic, payload in calls:
        if busy_by:
            with pytest.raises(BusRequestError, match="not_allowed"):
                await probe.request(topic, payload, timeout=5)
        else:
            await probe.request(topic, payload, timeout=5)


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

    assert [(each["id"], each["color"]) for each in await colours(made.series_id)] == [
        (made.source_ids[0], 0), (made.source_ids[1], 1), (third["id"], 2)]
    assert [each["color"] for each in await colours(other.series_id)] == [0]  # colours are of a series
    await probe.request("catalog.sources.remove", {"source_id": made.source_ids[1]}, timeout=5)
    fourth = await probe.request("catalog.sources.add", {
        "series_id": made.series_id, "source": source(url=ANIMAKIMA, source_type="web_video")}, timeout=5)
    by_id = {each["id"]: each["color"] for each in await colours(made.series_id)}
    assert by_id == {made.source_ids[0]: 0, third["id"]: 2, fourth["id"]: 1}  # the freed colour, the others kept
    with pytest.raises(BusRequestError):
        await probe.request("catalog.sources.update", {"source_id": third["id"], "fields": {"color": 5}}, timeout=5)
