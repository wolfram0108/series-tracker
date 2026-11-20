"""The composition module on a real bus with fake neighbours: the layout is counted from events, kept in the database,
published as a change and nothing else, and counted the same way whatever the cause.

The neighbours are modules too (`composition_fakes`); what a test waits for is an event or the end of a count, never
time. The one number below (STUCK_S) only ends a test that is stuck.
"""
import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest

from composition_fakes import (PAGE, FakeCatalog, FakeRules, FakeSources, FakeVoices, source)
from core.bus import Bus
from core.module import BaseModule, BusRequestError
from core.runner import Runner
from core.contracts import (CompositionPreviewReply, CompositionReply, Level, QueuesReply, ReleaseTable, RowState, RowVoice,
                            SettingsReset, SettingsSet, SettingsSetMany,
                            SourceStatus, TableRow, TableState, group_target, variant_target)
from core.db import Database
from core.reasons import parse
from modules.composition import CompositionModule
from schema_db import migrated

# A test that waits longer than this for an event is stuck: the event is the signal, the number only ends a broken test.
STUCK_S = 5.0


@pytest.fixture
def db_path(tmp_path):
    return migrated(tmp_path / "composition.db")


class Probe(BaseModule):
    name = "probe"


@dataclass
class System:
    bus: Bus
    probe: Probe
    catalog: FakeCatalog
    sources: FakeSources
    rules: FakeRules
    voices: FakeVoices
    composition: CompositionModule
    db_path: Path
    changed: object
    failed: object
    purged: object

    async def counted(self, action):
        """Runs `action`, which raises a cause, and returns when the count it raised has ended."""
        while not self.catalog.gets.empty():
            self.catalog.gets.get_nowait()
        made = action()
        await asyncio.wait_for(self.catalog.gets.get(), STUCK_S)
        await self.composition.idle()
        return made

    @staticmethod
    def taken(subscription) -> list[dict]:
        """What the subscription holds now: a count publishes before it ends, so nothing is awaited."""
        found = []
        while not subscription.queue.empty():
            found.append(subscription.queue.get_nowait().payload)
        return found

    @staticmethod
    async def next(subscription) -> dict:
        return (await asyncio.wait_for(subscription.queue.get(), STUCK_S)).payload

    async def get(self, series_id: int, **more) -> CompositionReply:
        reply = await self.probe.request("composition.get", {"series_id": series_id, **more}, timeout=STUCK_S)
        return CompositionReply.model_validate(reply)

    async def set(self, series_id: int, lever: str, value, *, season=None, episode=None, target=None) -> dict:
        command = SettingsSet(series_id=series_id, season=season, episode=episode, lever=lever, target=target,
                              value=value)
        return await self.probe.request("composition.settings.set", command.model_dump(mode="json"), timeout=STUCK_S)

    async def set_many(self, series_id: int, items: list[dict]) -> dict:
        command = SettingsSetMany(series_id=series_id, items=[SettingsSet(series_id=series_id, **item) for item in items])
        return await self.probe.request("composition.settings.set_many", command.model_dump(mode="json"), timeout=STUCK_S)

    async def reset(self, series_id: int, *, season=None, episode=None, lever=None, target=None) -> dict:
        command = SettingsReset(series_id=series_id, season=season, episode=episode, lever=lever, target=target)
        return await self.probe.request("composition.settings.reset", command.model_dump(mode="json"), timeout=STUCK_S)

    def profile_changed(self, profile_id: int = 7) -> None:
        self.probe.publish_event("rules.profile.changed", {"profile_id": profile_id})

    def sql(self, query: str, *params) -> list[tuple]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(query, params).fetchall()


@pytest.fixture
async def system(db_path):
    bus = Bus()
    catalog, sources, rules, voices = FakeCatalog(bus), FakeSources(bus), FakeRules(bus), FakeVoices(bus)
    composition = CompositionModule(bus, Database(str(db_path)))
    probe = Probe(bus)
    runner = Runner(bus, [catalog, sources, rules, voices, composition, probe])
    # The voice-over of the pages of these tests is a group's: only a row a group took is a row of the output.
    voices.set_groups({"Ruchime": ["Ruchime"]})
    await runner.start()
    made = System(bus, probe, catalog, sources, rules, voices, composition, db_path,
                  bus.subscribe("composition.changed"), bus.subscribe("composition.failed"),
                  bus.subscribe("series.purged"))
    yield made
    await runner.stop()


# --- tables of the fake site ------------------------------------------------------------------------------------

def row(key: str, text: str, *, player: str = "vk", group: str = "Сезон 1", voice: str = "Ruchime", **fields) -> TableRow:
    return TableRow(**{"key": key, "player": player, "player_label": player.upper(), "text": text, "group": group,
                       "voice": RowVoice(name=voice), "heights": [720, 1080], "state": RowState.PLAYABLE,
                       "link": f"https://cdn.test/{key}", **fields})


def page(*rows: TableRow, url: str = PAGE) -> ReleaseTable:
    return ReleaseTable(url=url, site="ruchime", names=["Асура"], rows=list(rows))


def season_rows(season: int, numbers, *, player: str = "vk", **fields) -> list[TableRow]:
    return [row(f"{player}-{season}-{number}", f"Серия {number}", player=player, group=f"Сезон {season}", **fields)
            for number in numbers]


def standard(url: str = PAGE) -> ReleaseTable:
    """Five episodes of season 1 and two of season 2."""
    return page(*season_rows(1, range(1, 6)), *season_rows(2, range(1, 3)), url=url)


def refs(*pairs: tuple[int, int]) -> list[dict]:
    return [{"season": season, "episode": episode} for season, episode in sorted(pairs)]


def season_one(*numbers: int) -> list[dict]:
    return refs(*[(1, number) for number in numbers])


def code_of(exc: BusRequestError) -> str:
    """The reason code of a refusal that travelled over the bus as `ValueError: {json}`."""
    return parse(str(exc).split(": ", 1)[1])["code"]


async def laid_out(s: System, table: ReleaseTable | None = None, **fields) -> int:
    """A series with one source whose page has been read; the events of getting there are taken away."""
    series_id = await s.counted(lambda: s.catalog.add_series([source(**fields)]))
    await s.counted(lambda: s.sources.finish(PAGE, table or standard()))
    s.taken(s.changed)
    return series_id


def only_source(s: System, series_id: int) -> int:
    return s.catalog.series[series_id]["sources"][0]["id"]


# --- M-12, M-14: the layout comes of the events, from the answer of the constructor --------------------------------

# --- M-13: a source without a profile ----------------------------------------------------------------------------

# --- M-14: whatever the cause, the layout is counted without the user, and counting twice changes nothing -----------

# --- M-15: groups and unknown voice-overs ---------------------------------------------------------------------------

def records_of(s: System, series_id: int) -> list[tuple]:
    return [(lever, target, json.loads(value)) for lever, target, value in s.sql(
        "SELECT lever, target, value FROM output_settings WHERE series_id = ?", series_id)]


@pytest.mark.asyncio
async def test_what_was_set_for_a_voice_over_follows_it_into_the_group_the_user_makes_and_back_out_of_it(system):
    s = system
    s.voices.groups = {"Стар": ["AniStar"]}
    series_id = await laid_out(s, page(row("a", "Серия 1", voice="AniStar"), row("b", "Серия 2", voice="Animy")))
    unknown = "Неизвестная озвучка 2"  # Animy took number 1 first, and a number is kept for good (П-7)
    source_id = only_source(s, series_id)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Стар"))
    await s.set(series_id, "reference.allow", False, season=1, target=variant_target("Стар", source_id, "vk"))
    await s.set(series_id, "reference.order", [{"group": "Стар", "source_id": source_id, "player": "vk"}])

    await s.counted(lambda: s.voices.set_groups({}))
    out = {(lever, target) for lever, target, _ in records_of(s, series_id)}
    assert ("voice.take", group_target(unknown)) in out and ("reference.allow", variant_target(unknown, source_id, "vk")) in out
    assert [value[0]["group"] for lever, _, value in records_of(s, series_id) if lever == "reference.order"] == [unknown]

    await s.counted(lambda: s.voices.set_groups({"Стар": ["AniStar"]}))
    back = {(lever, target) for lever, target, _ in records_of(s, series_id)}
    assert ("voice.take", group_target("Стар")) in back and ("reference.allow", variant_target("Стар", source_id, "vk")) in back
    assert not any(unknown in (target or "") for _, target in back)
    assert [value for lever, _, value in records_of(s, series_id) if lever == "reference.order"] == [
        [{"group": "Стар", "source_id": source_id, "player": "vk"}]]


# --- M-33: levers on the bus, C-13 to C-17 -------------------------------------------------------------------------

def choice(source_id: int, player: str = "vk") -> list:
    """An order of one variant the layouts of these tests have: the voice-over of the standard page, at a player of the source."""
    return [{"group": "Ruchime", "source_id": source_id, "player": player}]


def rich_standard(url: str = PAGE) -> ReleaseTable:
    """The standard page, with the same voice-over also at the players kodik and allvideo, for the levers that pick them."""
    return page(*season_rows(1, range(1, 6)), *season_rows(2, range(1, 3)), *season_rows(1, range(1, 6), player="kodik"),
                *season_rows(1, range(1, 6), player="allvideo"), url=url)


def levers_by_episode(reply: CompositionReply) -> dict:
    return {(e.season, e.episode): {x.lever: x for x in e.levers if not x.lever.startswith(("compilation.", "markup.", "reference.same", "subtitles."))}
            for e in reply.settings}


@pytest.mark.asyncio
async def test_resetting_an_episode_back_to_its_season_touches_that_episode_alone(system):
    """C-14."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    await s.set(series_id, "reference.order", choice(source_id), season=1)
    await s.set(series_id, "reference.order", choice(source_id, "kodik"), season=1, episode=3)
    s.taken(s.changed)
    assert await s.reset(series_id, season=1, episode=3) == {"ok": True}
    assert [event["episodes"] for event in s.taken(s.changed)] == [season_one(3)]
    follows = levers_by_episode(await s.get(series_id))[1, 3]
    assert [(x.value, x.level) for x in follows.values()] == [(choice(source_id), Level.SEASON)]
    assert await s.reset(series_id, season=1, episode=3) == {"ok": True} and s.taken(s.changed) == []


@pytest.mark.asyncio
async def test_a_command_the_registry_refuses_is_refused_with_its_reason_and_stores_nothing(system):
    s = system
    series_id = await laid_out(s)
    for lever, value, target, code in [("no.such", True, None, "unknown_lever"),
                                       ("reference.allow", True, None, "bad_target"),
                                       ("voice.take", "yes", group_target("Рутина"), "bad_value")]:
        with pytest.raises(BusRequestError) as caught:
            await s.set(series_id, lever, value, season=1, target=target)
        assert code_of(caught.value) == code
    with pytest.raises(BusRequestError) as caught:
        await s.reset(series_id, season=1, lever="reference.allow")
    assert code_of(caught.value) == "bad_target"
    with pytest.raises(BusRequestError) as caught:
        await s.set(999, "voice.take", True, target=group_target("Рутина"))
    assert "LookupError" in str(caught.value)
    assert s.sql("SELECT COUNT(*) FROM output_settings") == [(0,)] and s.taken(s.changed) == []


# --- Р-99: variants declared equal -----------------------------------------------------------------------------------

async def queues_of(s: System, series_id: int, **level) -> QueuesReply:
    return QueuesReply.model_validate(await s.probe.request(
        "composition.queues.get", {"series_id": series_id, **level}, timeout=STUCK_S))


@pytest.mark.asyncio
async def test_the_markup_that_follows_the_reference_makes_its_groups_its_own_with_the_queue_at_its_first_edit(system):
    """Stage 14 and Р-99: the groups the reference's queue shows are the groups of the markup's, as its order and its boxes are,
    when the first edit makes that queue its own; after it the two stand apart."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    same = [[*choice(source_id, "vk"), *choice(source_id, "kodik")]]
    await s.set(series_id, "reference.same", same, season=1)
    shown = await queues_of(s, series_id, season=1)
    assert shown.markup_follows and [each.same for each in shown.markup.rows] == [1, 1, None]
    await s.set(series_id, "markup.allow", False, season=1, target=variant_target("Ruchime", source_id, "allvideo"))
    shown = await queues_of(s, series_id, season=1)
    assert not shown.markup_follows and [each.same for each in shown.markup.rows] == [1, 1, None]
    assert shown.markup.same_level == Level.SEASON
    await s.reset(series_id, season=1, lever="reference.same")
    shown = await queues_of(s, series_id, season=1)
    assert [each.same for each in shown.reference.rows] == [None, None, None] and [each.same for each in shown.markup.rows] == [1, 1, None]


# --- several levers in one command: one write, one revision, one event ----------------------------------------------

# --- M-34: a lever and a consumer added through the registries alone -------------------------------------------------

# --- C-1, C-2: a cause that comes while the series is being counted --------------------------------------------------

# --- a series that is deleted -----------------------------------------------------------------------------------------

# --- C-9: two series on one page ------------------------------------------------------------------------------------

# --- C-10: a read that failed ---------------------------------------------------------------------------------------

# --- a neighbour that does not answer -------------------------------------------------------------------------------

# --- C-8, I-21: the module stops and starts ---------------------------------------------------------------------------

# --- the race of an event ahead of the answer it belongs to ----------------------------------------------------------------

# --- the tables --------------------------------------------------------------------------------------------------------

# --- the time of a reading is no change ------------------------------------------------------------------------------------

# --- the headers of the levels ---------------------------------------------------------------------------------------------

def headers(reply: CompositionReply) -> dict:
    return {level.season: {x.lever: (x.value, x.level) for x in level.levers} for level in reply.levels}


# --- composition.recompute -----------------------------------------------------------------------------------------------------

async def recompute(s: System, series_id: int) -> dict:
    return await s.probe.request("composition.recompute", {"series_id": series_id}, timeout=STUCK_S)


@pytest.mark.asyncio
async def test_a_series_the_catalog_does_not_have_refuses_the_request_and_so_does_a_deleted_one(system):
    s = system
    with pytest.raises(BusRequestError) as caught:
        await recompute(s, 999)
    assert parse(str(caught.value).split(": ", 1)[1])["params"] == {"topic": "catalog.series.get"}

    series_id = await laid_out(s)
    s.catalog.delete_series(series_id)
    await s.next(s.purged)
    with pytest.raises(BusRequestError) as caught:
        await recompute(s, series_id)
    assert "LookupError" in str(caught.value)


# --- composition.preview: an address before it is a source of a series -------------------------------------------------------

COMPOSITION_TABLES = ("composition_rows", "composition_state", "composition_sources", "unknown_voices", "output_settings")


async def preview(s: System, url: str = PAGE, **more) -> CompositionPreviewReply:
    reply = await s.probe.request("composition.preview", {"url": url, **more}, timeout=STUCK_S)
    return CompositionPreviewReply.model_validate(reply)


def nothing_was_written(s: System) -> None:
    assert all(s.sql(f"SELECT COUNT(*) FROM {table}") == [(0,)] for table in COMPOSITION_TABLES)
    assert s.taken(s.changed) == [] and s.taken(s.failed) == [] and s.catalog.gets.empty() and not s.composition._flights


@pytest.mark.asyncio
async def test_an_address_with_a_profile_shows_the_answer_of_the_constructor_for_every_line(system):
    s = system
    s.sources.tables[PAGE] = page(
        row("a", "Серия 1 [Мой дубляж]", group=""), row("b", "1-3 серия", group="Сезон 2"), row("t", "Трейлер", group=""),
        row("n", "Спецвыпуск", group="")).model_copy(update={"url": PAGE, "complete": True})
    reply = await preview(s, profile_id=7, series_season="4")
    assert reply.summary.status == SourceStatus.READY
    got = {each.key: each.result for each in reply.lines}
    assert (got["a"].season, got["a"].episode, got["a"].voiceover) == (4, 1, "Мой дубляж")
    assert (got["b"].season, got["b"].start, got["b"].end) == (2, 1, 3)
    assert got["t"].excluded and got["n"].episode is None and not got["n"].excluded
    [call] = s.rules.calls
    assert (call.profile_id, call.series_season, [line.key for line in call.lines]) == (7, "4", ["a", "b", "t", "n"])
    assert [(r.source_id, r.row_key, r.season, r.episode_start, r.episode_end, r.voice_name) for r in reply.rows] == [
        (0, "a", 4, 1, 1, "Мой дубляж"), (0, "b", 2, 1, 3, "Ruchime")]
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_lines_the_window_holds_get_the_answer_of_the_profile_and_no_page_is_read(system):
    s = system
    reply = await s.probe.request("composition.preview", {
        "lines": ["Серия 1 [Мой дубляж]", "Трейлер"], "profile_id": 7, "series_season": "4"}, timeout=STUCK_S)
    reply = CompositionPreviewReply.model_validate(reply)
    assert (reply.state, reply.summary, reply.names) == (TableState.READY, None, ())
    assert [(each.key, each.line) for each in reply.lines] == [("0", "Серия 1 [Мой дубляж]"), ("1", "Трейлер")]
    assert [each.heights for each in reply.lines] == [(), ()]
    [call] = s.rules.calls
    assert (call.profile_id, call.series_season, [(l.key, l.text) for l in call.lines]) == (
        7, "4", [("0", "Серия 1 [Мой дубляж]"), ("1", "Трейлер")])
    assert s.sources.requests == [] and s.sources.reads_begun == []
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_a_request_that_asks_for_no_read_of_a_page_nothing_was_read_of_gives_the_state_alone(system):
    s = system
    reply = await preview(s, refresh=False)
    assert (reply.state, reply.reason, reply.summary, reply.lines) == (TableState.ABSENT, None, None, ())
    assert s.sources.reads_begun == [] and s.sources.requests[-1].refresh is False
    nothing_was_written(s)


# --- what may be picked: the table of what is allowed in each state of a variant ---------------------------------------

# --- a sync of the series is one run: its layout is the snapshot, its levers are not changed (stage 11) ---------------

# --- the order of the voice-overs of a season (stage 11) ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_order_of_the_voices_of_a_season_is_shown_kept_and_set_by_the_user_only(system):
    from core.contracts import RUN_CHANGED
    s = system
    s.voices.groups = {"Ruchime": ["Ruchime"], "AniStar": ["AniStar"]}
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4)),
                                       *season_rows(1, range(1, 3), player="kodik", voice="AniStar")))
    [order] = (await s.get(series_id)).voice_orders
    labels = [voice.label for voice in order.voices]
    assert order.season == 1 and len(labels) == 2 and all(voice.episodes for voice in order.voices)

    wanted = list(reversed(labels))
    changed = s.bus.subscribe("composition.changed")
    await s.probe.request("composition.voice_order.set", {"series_id": series_id, "season": 1, "labels": wanted},
                          timeout=STUCK_S)
    assert [voice.label for voice in (await s.get(series_id)).voice_orders[0].voices] == wanted
    assert s.taken(changed)  # the containers follow the order at the next sync: consumers hear of it

    with pytest.raises(BusRequestError, match="named twice"):
        await s.probe.request("composition.voice_order.set", {"series_id": series_id, "season": 1,
                                                               "labels": [labels[0], labels[0]]}, timeout=STUCK_S)
    # A voice-over no source gives may still be in the files: the window shows it and the user moves it too.
    await s.probe.request("composition.voice_order.set", {"series_id": series_id, "season": 1,
                                                           "labels": ["Held", *labels]}, timeout=STUCK_S)
    [order] = (await s.get(series_id)).voice_orders
    assert [(voice.label, bool(voice.episodes)) for voice in order.voices] == [
        ("Held", False), *((label, True) for label in labels)]
    s.probe.publish_event(RUN_CHANGED, {"series_id": series_id, "active": True})
    await s.bus.settled()
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.probe.request("composition.voice_order.set", {"series_id": series_id, "season": 1, "labels": labels},
                              timeout=STUCK_S)


@pytest.mark.asyncio
async def test_the_name_of_the_track_of_a_voice_is_kept_for_the_series_taken_back_and_set_by_the_user_only(system):
    from core.contracts import RUN_CHANGED
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3), player="kodik", voice="AniStar")))
    assert (await s.get(series_id)).voice_titles == {}
    changed = s.bus.subscribe("composition.changed")
    await s.probe.request("composition.voice_title.set", {"series_id": series_id, "label": "AniStar", "title": "ПМ_AniStar"},
                          timeout=STUCK_S)
    assert (await s.get(series_id)).voice_titles == {"AniStar": "ПМ_AniStar"}
    assert s.taken(changed)  # the containers take the name at the next sync: consumers hear of it
    # A voice-over no source gives may still be in the files: it is named too.
    await s.probe.request("composition.voice_title.set", {"series_id": series_id, "label": "Held", "title": "Старая"},
                          timeout=STUCK_S)
    for back in ("", "AniStar"):  # an empty name, or the name of the group, takes the user's name back
        await s.probe.request("composition.voice_title.set", {"series_id": series_id, "label": "AniStar", "title": back},
                              timeout=STUCK_S)
        assert (await s.get(series_id)).voice_titles == {"Held": "Старая"}
    with pytest.raises(BusRequestError, match="one line"):
        await s.probe.request("composition.voice_title.set", {"series_id": series_id, "label": "AniStar",
                                                               "title": "две\nстроки"}, timeout=STUCK_S)
    s.probe.publish_event(RUN_CHANGED, {"series_id": series_id, "active": True})
    await s.bus.settled()
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.probe.request("composition.voice_title.set", {"series_id": series_id, "label": "AniStar", "title": "x"},
                              timeout=STUCK_S)


# --- the queues of a level, as the window lists them -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_markup_follows_the_reference_until_its_first_edit_copies_it_and_then_stands_apart(system):
    """Stage 14: an untouched queue of the markup reference is the queue of the reference, however it changes; the first
    edit on a level writes the queue as it was shown (its order and the variants left out) and the edit; after it a new
    order of the reference does not move the markup; «как уровнем выше» takes the level back to following."""
    s = system
    series_id = await laid_out(s, page(row("a", "Серия 1"), row("b", "Серия 1", player="kodik"), row("c", "Серия 2")))
    source_id = only_source(s, series_id)
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["ruchime"]}))
    vk, kodik = ({"group": "Рутина", "source_id": source_id, "player": player} for player in ("vk", "kodik"))

    async def queues(**level):
        return QueuesReply.model_validate(await s.probe.request(
            "composition.queues.get", {"series_id": series_id, **level}, timeout=STUCK_S))

    def players(view):
        return [(each.player, each.allowed) for each in view.rows]

    shown = await queues(season=1)
    assert shown.markup_follows and players(shown.markup) == players(shown.reference) == [("vk", True), ("kodik", True)]
    await s.set(series_id, "reference.order", [kodik], season=1)
    await s.set(series_id, "reference.allow", False, season=1, target=variant_target("Рутина", source_id, "vk"))
    shown = await queues(season=1)
    assert shown.markup_follows and players(shown.markup) == [("kodik", True), ("vk", False)]  # it follows the reference

    # The first edit: the box of kodik. The copy keeps the order and the box of vk the reference had.
    await s.set(series_id, "markup.allow", False, season=1, target=variant_target("Рутина", source_id, "kodik"))
    shown = await queues(season=1)
    assert not shown.markup_follows and shown.markup.order_level == Level.SEASON
    assert players(shown.markup) == [("kodik", False), ("vk", False)]
    await s.set(series_id, "reference.order", [vk], season=1)
    await s.set(series_id, "reference.allow", True, season=1, target=variant_target("Рутина", source_id, "vk"))
    shown = await queues(season=1)
    assert players(shown.reference) == [("vk", True), ("kodik", True)]
    assert players(shown.markup) == [("kodik", False), ("vk", False)]  # apart from the reference now
    assert not (await queues(season=1, episode=1)).markup_follows  # an episode inherits the queue of its season
    assert (await queues()).markup_follows  # the series above still follows

    await s.probe.request("composition.settings.reset", SettingsReset(
        series_id=series_id, season=1, queue="markup").model_dump(mode="json"), timeout=STUCK_S)
    shown = await queues(season=1)
    assert shown.markup_follows and players(shown.markup) == players(shown.reference)


# --- the reads of the pages of the series ------------------------------------------------------------------------
