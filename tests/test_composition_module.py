"""The composition module on a real bus with fake neighbours: the layout is counted from events, kept in the database,
published as a change and nothing else, and counted the same way whatever the cause.

The neighbours are modules too (`composition_fakes`); what a test waits for is an event or the end of a count, never
time. The one number below (STUCK_S) only ends a test that is stuck.
"""
import asyncio
import json
import importlib.util
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from composition_fakes import (HOST, OTHER_PAGE, PAGE, FakeCatalog, FakeRules, FakeSources, FakeVoices, lecturer,
                               source)
from core import BaseModule, Bus, BusRequestError, Runner
from core.contracts import (CompositionPreviewReply, CompositionReply, EffectiveLever, Level, ReleaseTable, RowState,
                            TargetKind, ValueShape,
                            RowVoice, SettingsReset, SettingsSet, SourceStatus, TableRow, TableState,
                            group_target, variant_target)
from core.db import Database
from core.reasons import parse, reason
from modules.composition import CompositionModule, levers
from modules.composition.levers import Lever

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = [ROOT / "alembic" / "versions" / name
              for name in ("0027_composition_tables.py", "0035_composition_source_exclusion.py")]
# A test that waits longer than this for an event is stuck: the event is the signal, the number only ends a broken test.
STUCK_S = 5.0


def migrate(path: Path, direction: str) -> None:
    """The migrations of this package alone, so that the migrations of other packages cannot break these tests."""
    engine = create_engine(f"sqlite:///{path}")
    for file in MIGRATIONS if direction == "upgrade" else reversed(MIGRATIONS):
        spec = importlib.util.spec_from_file_location(f"migration_{file.stem}", file)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                getattr(migration, direction)()
    engine.dispose()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "composition.db"
    migrate(path, "upgrade")
    return path


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

@pytest.mark.asyncio
async def test_a_new_series_is_counted_waiting_and_laid_out_when_its_table_is_ready(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source()]))
    assert s.taken(s.changed) == [{"series_id": series_id, "rev": 1, "episodes": [], "summary_only": True}]
    waiting = await s.get(series_id)
    assert (waiting.rev, waiting.rows, waiting.settings) == (1, [], [])
    assert [(each.status, each.players) for each in waiting.sources] == [(SourceStatus.WAITING, [])]
    assert [request.refresh for request in s.sources.requests] == [True] and s.rules.calls == []

    await s.counted(lambda: s.sources.finish(PAGE, standard()))
    assert s.taken(s.changed) == [{"series_id": series_id, "rev": 2, "summary_only": False,
                                   "episodes": refs(*[(1, n) for n in range(1, 6)], (2, 1), (2, 2))}]
    ready = await s.get(series_id)
    assert ready.rev == 2 and ready.sources[0].status == SourceStatus.READY
    assert [(r.season, r.episode_start, r.episode_end, r.voice_label) for r in ready.rows] == [
        *[(1, n, n, "Неизвестная озвучка 1") for n in range(1, 6)],
        (2, 1, 1, "Неизвестная озвучка 1"), (2, 2, 2, "Неизвестная озвучка 1")]
    assert [request.refresh for request in s.sources.requests] == [True, False]


@pytest.mark.asyncio
async def test_every_number_comes_from_the_answer_of_the_constructor_and_is_stored_as_it_was_given(system):
    s = system
    series_id = await laid_out(s)
    [call] = s.rules.calls
    assert call.profile_id == 7 and call.series_season is None
    assert call.lines[0].key == "vk-1-1" and call.lines[0].text == "PID:fake.test | P:VK | Асура | Серия 1 | G:Сезон 1 | V:Ruchime | R:1080p"
    given = [(line.key, lecturer(line.text)) for line in call.lines]
    stored = s.sql("SELECT row_key, season, episode_start, episode_end FROM composition_rows WHERE series_id = ?"
                   " ORDER BY seq", series_id)
    assert stored == [(key, found["season"], found["episode"], found["episode"]) for key, found in given]


@pytest.mark.asyncio
async def test_the_constructor_is_asked_once_per_source_with_all_its_lines_and_the_season_of_the_card(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series(
        [source(PAGE), source(OTHER_PAGE, parser_profile_id=8), source(f"{HOST}/p/3", parser_profile_id=None)],
        season="3"))
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 4)))))
    await s.counted(lambda: s.sources.finish(OTHER_PAGE, page(row("o", "Серия 1", group=""), url=OTHER_PAGE)))
    await s.counted(lambda: s.sources.finish(f"{HOST}/p/3", page(row("p", "Серия 1"), url=f"{HOST}/p/3")))
    assert {(call.profile_id, len(call.lines), call.series_season) for call in s.rules.calls} == {
        (7, 3, "3"), (8, 1, "3")}
    reply = await s.get(series_id)
    assert {(r.source_id, r.season) for r in reply.rows} == {(1, 1), (2, 3)}
    assert [each.status for each in reply.sources] == [SourceStatus.READY, SourceStatus.READY, SourceStatus.NO_PROFILE]


@pytest.mark.asyncio
async def test_a_page_is_noted_by_the_address_the_sources_module_gave_not_by_the_one_in_the_series(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source(f"{PAGE}?utm=1")]))
    assert s.sql("SELECT table_url FROM composition_sources WHERE series_id = ?", series_id) == [(PAGE,)]
    await s.counted(lambda: s.sources.finish(f"{PAGE}?utm=2", standard()))
    assert (await s.get(series_id)).sources[0].status == SourceStatus.READY


@pytest.mark.asyncio
async def test_a_torrent_source_is_none_of_the_business_of_the_module(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series(
        [source("https://kinozal.tv/details.php?id=1", source_type="torrent")]))
    assert s.taken(s.changed) == [] and s.taken(s.failed) == [] and s.sources.requests == []
    assert s.sql("SELECT COUNT(*) FROM composition_state") == [(0,)]
    assert (await s.get(series_id)).rev == 0


@pytest.mark.asyncio
async def test_an_update_of_a_series_that_leaves_its_season_alone_counts_nothing(system):
    s = system
    series_id = await laid_out(s)
    s.probe.publish_event("series.updated", {"series_id": series_id, "name": "Другое имя", "statuses": [],
                                             "is_busy": False})
    await s.get(series_id)  # the event is ahead of this query on the bus, and its handler does not wait
    assert s.composition._flights == {}


# --- M-13: a source without a profile ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_source_without_a_profile_is_only_a_summary_until_a_profile_is_assigned(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source(parser_profile_id=None)]))
    await s.counted(lambda: s.sources.finish(PAGE, standard()))
    assert [event["summary_only"] for event in s.taken(s.changed)] == [True, True]
    bare = await s.get(series_id)
    assert bare.rows == [] and bare.settings == [] and s.rules.calls == []
    summary = bare.sources[0]
    assert summary.status == SourceStatus.NO_PROFILE and summary.reason is None
    [player] = summary.players
    assert (player.player, player.rows, player.groups) == ("vk", 7, 2)
    assert s.voices.asked == []

    await s.counted(lambda: s.catalog.update_source(only_source(s, series_id), parser_profile_id=7))
    [event] = s.taken(s.changed)
    assert event["rev"] == 3 and len(event["episodes"]) == 7 and event["summary_only"] is False
    assert len((await s.get(series_id)).rows) == 7 and s.rules.calls


# --- M-14: whatever the cause, the layout is counted without the user, and counting twice changes nothing -----------

@pytest.mark.asyncio
async def test_a_new_table_is_counted_and_names_the_episodes_that_differ(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    rev = (await s.get(series_id)).rev
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 5)))))
    assert s.taken(s.changed) == [{"series_id": series_id, "rev": rev + 1, "episodes": season_one(4),
                                   "summary_only": False}]


@pytest.mark.asyncio
async def test_a_changed_rule_of_the_profile_is_counted_for_the_series_that_read_it(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    s.rules.profiles[7] = lambda text: {**lecturer(text), "episode": lecturer(text)["episode"] + 10}
    await s.counted(s.profile_changed)
    [event] = s.taken(s.changed)
    assert event["episodes"] == season_one(1, 2, 3, 11, 12, 13)
    assert [r.episode_start for r in (await s.get(series_id)).rows] == [11, 12, 13]


@pytest.mark.asyncio
async def test_a_profile_that_no_source_of_ours_reads_counts_nothing(system):
    s = system
    await laid_out(s)
    s.profile_changed(profile_id=99)
    await s.counted(s.profile_changed)  # the profile of the source: one count, and it changes nothing
    assert s.catalog.gets.empty() and s.taken(s.changed) == []


@pytest.mark.asyncio
async def test_new_groups_of_voice_overs_are_counted_for_every_series(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["ruchime"]}))
    [event] = s.taken(s.changed)
    assert event["episodes"] == season_one(1, 2, 3)
    assert {r.voice_group for r in (await s.get(series_id)).rows} == {"Рутина"}


@pytest.mark.asyncio
async def test_the_choice_made_for_a_source_is_counted(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    source_id = only_source(s, series_id)
    await s.counted(lambda: s.catalog.update_source(source_id, players_off=["vk"]))
    [event] = s.taken(s.changed)
    off = await s.get(series_id)
    assert off.rows == [] and event["episodes"] == season_one(1, 2, 3)
    # The rows the choice left out stay in the layout, excluded by the source: their videos are there and not wanted.
    left = (await s.probe.request("composition.get", {"series_id": series_id, "include_excluded": True},
                                  timeout=STUCK_S))["rows"]
    assert [(row["excluded"], row["exclusion"]) for row in left] == [(True, "source")] * 3
    assert [player.rows for player in off.sources[0].players] == [3]
    await s.counted(lambda: s.catalog.update_source(source_id, players_off=[]))
    assert len((await s.get(series_id)).rows) == 3


@pytest.mark.asyncio
async def test_the_season_of_the_card_is_the_season_of_rows_the_constructor_gave_none_and_season_one_is_the_last_resort(
        system):
    s = system
    s.rules.profiles[7] = lambda text: {key: value for key, value in lecturer(text).items() if key != "season"}
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    assert {r.season for r in (await s.get(series_id)).rows} == {1}  # the rules and the card are silent
    await s.counted(lambda: s.catalog.set_season(series_id, "2"))
    assert {r.season for r in (await s.get(series_id)).rows} == {2}  # the card wins over the default
    last = s.taken(s.changed)[-1]
    assert last["episodes"] == refs((1, 1), (1, 2), (2, 1), (2, 2)) and last["summary_only"] is False


@pytest.mark.asyncio
async def test_a_series_whose_card_names_a_season_lays_out_the_rows_without_one_in_that_season(system):
    s = system
    s.rules.profiles[7] = lambda text: {key: value for key, value in lecturer(text).items() if key != "season"}
    series_id = await s.counted(lambda: s.catalog.add_series([source()], season="3"))
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 3)))))
    assert {r.season for r in (await s.get(series_id)).rows} == {3}
    assert [e.season for e in (await s.get(series_id)).settings] == [3, 3]


@pytest.mark.asyncio
async def test_the_same_count_again_gives_the_same_rows_and_publishes_nothing(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["ruchime"]}))
    s.taken(s.changed)
    before = await s.get(series_id)
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["ruchime"]}))
    await s.counted(s.profile_changed)
    await s.counted(lambda: s.catalog.update_source(only_source(s, series_id)))
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 4)))))
    after = await s.get(series_id)
    assert s.taken(s.changed) == [] and s.taken(s.failed) == []
    assert (after.rev, after.rows, after.sources, after.settings) == (before.rev, before.rows, before.sources,
                                                                      before.settings)


@pytest.mark.asyncio
async def test_a_row_that_leaves_the_table_leaves_the_layout_with_its_episode_named_in_the_change(system):
    """C-11."""
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, [1, 3]))))
    assert s.taken(s.changed)[0]["episodes"] == season_one(2)
    assert [r.episode_start for r in (await s.get(series_id)).rows] == [1, 3]


@pytest.mark.asyncio
async def test_a_compilation_is_one_row_and_a_change_of_it_touches_every_episode_of_its_range(system):
    s = system
    series_id = await laid_out(s, page(row("c", "1-3 серия", player="kodik"), *season_rows(1, [4])))
    [compilation, single] = (await s.get(series_id)).rows
    assert (compilation.episode_start, compilation.episode_end, compilation.is_compilation) == (1, 3, True)
    assert (single.episode_start, single.episode_end) == (4, 4)
    await s.counted(lambda: s.sources.finish(PAGE, page(row("c", "1-3 серия", player="kodik", heights=[360]),
                                                         *season_rows(1, [4]))))
    assert s.taken(s.changed)[0]["episodes"] == season_one(1, 2, 3)


@pytest.mark.asyncio
async def test_rows_the_constructor_excluded_are_kept_and_given_only_on_request(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, [1]), row("t", "Трейлер"), row("x", "Спецвыпуск")))
    shown = await s.get(series_id)
    assert [r.row_key for r in shown.rows] == ["vk-1-1"] and [e.episode for e in shown.settings] == [1]
    everything = await s.get(series_id, include_excluded=True)
    assert [(r.row_key, r.excluded, r.exclusion) for r in everything.rows] == [
        ("vk-1-1", False, None), ("t", True, "rule"), ("x", True, "no_number")]


# --- M-15: groups and unknown voice-overs ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_overs_are_in_groups_or_unknown_with_a_number_that_holds(system):
    s = system
    tbl = page(row("a", "Серия 1", voice="AniStar"), row("b", "Серия 2", voice="Animy"),
               row("c", "Серия 3", voice="anistar"))
    series_id = await laid_out(s, tbl)
    assert [(r.voice_group, r.voice_label, r.voice_unknown) for r in (await s.get(series_id)).rows] == [
        (None, "Неизвестная озвучка 1", 1), (None, "Неизвестная озвучка 2", 2), (None, "Неизвестная озвучка 1", 1)]

    await s.counted(lambda: s.voices.set_groups({"Стар": ["AniStar"]}))
    assert [(r.voice_group, r.voice_label, r.voice_unknown) for r in (await s.get(series_id)).rows] == [
        ("Стар", "Стар", None), (None, "Неизвестная озвучка 2", 2), ("Стар", "Стар", None)]

    await s.counted(lambda: s.voices.set_groups({}))
    assert [r.voice_unknown for r in (await s.get(series_id)).rows] == [1, 2, 1]
    assert s.sql("SELECT name_key, number FROM unknown_voices WHERE series_id = ? ORDER BY number", series_id) == [
        ("anistar", 1), ("animy", 2)]


def records_of(s: System, series_id: int) -> list[tuple]:
    return [(lever, target, json.loads(value)) for lever, target, value in s.sql(
        "SELECT lever, target, value FROM output_settings WHERE series_id = ?", series_id)]


@pytest.mark.asyncio
async def test_what_was_set_for_a_voice_over_follows_it_into_the_group_the_user_makes_and_back_out_of_it(system):
    s = system
    series_id = await laid_out(s, page(row("a", "Серия 1", voice="AniStar"), row("b", "Серия 2", voice="Animy")))
    unknown = "Неизвестная озвучка 1"
    source_id = only_source(s, series_id)
    await s.set(series_id, "voice.take", False, season=1, target=group_target(unknown))
    await s.set(series_id, "variant.allow", False, season=1, target=variant_target(unknown, source_id, "vk"))
    await s.set(series_id, "reference.choice", {"group": unknown, "source_id": source_id, "player": "vk"})
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Неизвестная озвучка 2"))

    await s.counted(lambda: s.voices.set_groups({"Стар": ["AniStar"]}))
    made = {(lever, target) for lever, target, _ in records_of(s, series_id)}
    assert ("voice.take", group_target("Стар")) in made and ("variant.allow", variant_target("Стар", source_id, "vk")) in made
    assert ("voice.take", group_target("Неизвестная озвучка 2")) in made  # another voice-over keeps its own
    assert not any(unknown in (target or "") for _, target in made)
    assert [value for lever, _, value in records_of(s, series_id) if lever == "reference.choice"] == [
        {"group": "Стар", "source_id": source_id, "player": "vk"}]

    await s.counted(lambda: s.voices.set_groups({}))
    back = {(lever, target) for lever, target, _ in records_of(s, series_id)}
    assert ("voice.take", group_target(unknown)) in back and ("variant.allow", variant_target(unknown, source_id, "vk")) in back
    assert [value["group"] for lever, _, value in records_of(s, series_id) if lever == "reference.choice"] == [unknown]


@pytest.mark.asyncio
async def test_the_name_the_constructor_assigned_beats_the_one_of_the_player_and_is_the_one_asked_about(system):
    s = system
    s.voices.groups = {"Моя": ["Мой дубляж"], "Стар": ["AniStar"]}
    series_id = await laid_out(s, page(row("a", "Серия 1 [Мой дубляж]", voice="AniStar"),
                                       row("b", "Серия 2", voice="AniStar")))
    reply = await s.get(series_id)
    assert [r.voice_group for r in reply.rows] == ["Моя", "Стар"]
    assert set(s.voices.asked[-1]) == {"AniStar", "Мой дубляж"}
    assert [r.voice_name for r in reply.rows] == ["Мой дубляж", "AniStar"]
    assert s.sql("SELECT voice_name FROM composition_rows WHERE series_id = ? ORDER BY seq", series_id) == [
        ("Мой дубляж",), ("AniStar",)]


@pytest.mark.asyncio
async def test_a_series_whose_names_are_all_empty_asks_the_groups_nothing(system):
    s = system
    await laid_out(s, page(row("a", "Серия 1", voice="")))
    assert s.voices.asked == []


# --- M-33: levers on the bus, C-13 to C-17 -------------------------------------------------------------------------

def choice(source_id: int, player: str = "vk") -> dict:
    """A variant the layouts of these tests have: the voice-over `Ruchime` of the standard page, at a player of the source."""
    return {"group": "Неизвестная озвучка 1", "source_id": source_id, "player": player}


def rich_standard(url: str = PAGE) -> ReleaseTable:
    """The standard page, with the same voice-over also at the players kodik and allvideo, for the levers that pick them."""
    return page(*season_rows(1, range(1, 6)), *season_rows(2, range(1, 3)), *season_rows(1, range(1, 6), player="kodik"),
                *season_rows(1, range(1, 6), player="allvideo"), url=url)


def levers_by_episode(reply: CompositionReply) -> dict:
    return {(e.season, e.episode): {x.lever: x for x in e.levers} for e in reply.settings}


@pytest.mark.asyncio
async def test_a_season_value_reaches_the_episodes_that_follow_it_and_not_the_one_with_a_value_of_its_own(system):
    """C-13."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    assert await s.set(series_id, "reference.choice", choice(source_id), season=1) == {"ok": True}
    assert s.taken(s.changed)[0]["episodes"] == season_one(1, 2, 3, 4, 5)

    await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=3)
    await s.set(series_id, "variant.allow", False, season=1, episode=3, target=variant_target("Ruchime", source_id, "vk"))
    assert [event["episodes"] for event in s.taken(s.changed)] == [season_one(3), season_one(3)]

    await s.set(series_id, "reference.choice", choice(source_id, "allvideo"), season=1)
    assert [event["episodes"] for event in s.taken(s.changed)] == [season_one(1, 2, 4, 5)]
    at = levers_by_episode(await s.get(series_id))
    assert (at[1, 3]["reference.choice"].value, at[1, 3]["reference.choice"].level) == (choice(source_id, "kodik"),
                                                                                         Level.EPISODE)
    assert (at[1, 4]["reference.choice"].value, at[1, 4]["reference.choice"].level) == (choice(source_id, "allvideo"),
                                                                                         Level.SEASON)
    assert at[2, 1]["reference.choice"].level == Level.DEFAULT


@pytest.mark.asyncio
async def test_resetting_an_episode_back_to_its_season_touches_that_episode_alone(system):
    """C-14."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    await s.set(series_id, "reference.choice", choice(source_id), season=1)
    await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=3)
    s.taken(s.changed)
    assert await s.reset(series_id, season=1, episode=3) == {"ok": True}
    assert [event["episodes"] for event in s.taken(s.changed)] == [season_one(3)]
    follows = levers_by_episode(await s.get(series_id))[1, 3]
    assert [(x.value, x.level) for x in follows.values()] == [(choice(source_id), Level.SEASON)]
    assert await s.reset(series_id, season=1, episode=3) == {"ok": True} and s.taken(s.changed) == []


@pytest.mark.asyncio
async def test_a_voice_off_on_the_season_and_on_for_one_episode_is_taken_for_that_episode_only(system):
    """C-15."""
    s = system
    series_id = await laid_out(s)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Рутина"))
    await s.set(series_id, "voice.take", True, season=1, episode=2, target=group_target("Рутина"))
    assert [event["episodes"] for event in s.taken(s.changed)] == [season_one(1, 2, 3, 4, 5), season_one(2)]
    at = levers_by_episode(await s.get(series_id))
    assert (at[1, 2]["voice.take"].value, at[1, 2]["voice.take"].level) == (True, Level.EPISODE)
    assert (at[1, 3]["voice.take"].value, at[1, 3]["voice.take"].level) == (False, Level.SEASON)
    assert "voice.take" not in at[2, 1]


@pytest.mark.asyncio
async def test_a_reset_of_one_lever_takes_that_lever_back_and_leaves_the_others_of_the_level(system):
    s = system
    series_id = await laid_out(s)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Рутина"))
    await s.set(series_id, "reference.choice", choice(only_source(s, series_id)), season=1)
    s.taken(s.changed)
    await s.reset(series_id, season=1, lever="voice.take", target=group_target("Рутина"))
    assert s.taken(s.changed)[0]["episodes"] == season_one(1, 2, 3, 4, 5)
    assert s.sql("SELECT lever FROM output_settings WHERE series_id = ?", series_id) == [("reference.choice",)]


@pytest.mark.asyncio
async def test_a_source_that_is_removed_takes_the_settings_that_point_at_it_and_its_rows(system):
    """C-16, C-3."""
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source(PAGE), source(OTHER_PAGE)]))
    first, second = [each["id"] for each in s.catalog.series[series_id]["sources"]]
    await s.counted(lambda: s.sources.finish(PAGE, standard()))
    await s.counted(lambda: s.sources.finish(OTHER_PAGE, page(*season_rows(1, range(1, 4), player="kodik"),
                                                              url=OTHER_PAGE)))
    await s.set(series_id, "reference.choice", choice(second, "kodik"), season=1)
    await s.set(series_id, "variant.allow", False, season=1, episode=2, target=variant_target("Ruchime", second, "kodik"))
    await s.set(series_id, "variant.allow", False, season=1, target=variant_target("Ruchime", first, "vk"))
    s.taken(s.changed)

    await s.counted(lambda: s.catalog.remove_source(second))
    [event] = s.taken(s.changed)
    reply = await s.get(series_id)
    assert {r.source_id for r in reply.rows} == {first} and len(reply.rows) == 7
    assert event["episodes"] == season_one(1, 2, 3, 4, 5)
    assert s.sql("SELECT lever, target FROM output_settings WHERE series_id = ?", series_id) == [
        ("variant.allow", variant_target("Ruchime", first, "vk"))]
    assert s.sql("SELECT source_id FROM composition_sources WHERE series_id = ?", series_id) == [(first,)]
    assert levers_by_episode(reply)[1, 2]["reference.choice"].level == Level.DEFAULT


@pytest.mark.asyncio
async def test_an_episode_that_leaves_the_layout_takes_its_own_settings_and_the_numbers_keep_the_rest(system):
    """C-17."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=5)
    await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=2)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Рутина"))
    s.taken(s.changed)
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 4)), *season_rows(2, [1]),
                                                        *season_rows(1, range(1, 4), player="kodik"),
                                                        *season_rows(1, range(1, 4), player="allvideo"))))
    assert s.sql("SELECT season, episode, lever FROM output_settings WHERE series_id = ?"
                 " ORDER BY season, episode, lever", series_id) == [(1, None, "voice.take"),
                                                                   (1, 2, "reference.choice")]
    assert s.taken(s.changed)[0]["episodes"] == refs((1, 4), (1, 5), (2, 2))


@pytest.mark.asyncio
async def test_the_settings_of_a_series_without_an_answered_source_are_not_taken_away_with_its_layout(system):
    """C-12: a profile taken off for a while must not wipe what the user set per episode."""
    s = system
    series_id = await laid_out(s, rich_standard())
    source_id = only_source(s, series_id)
    await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=2)
    await s.counted(lambda: s.catalog.update_source(source_id, parser_profile_id=None))
    reply = await s.get(series_id)
    assert reply.rows == [] and reply.sources[0].status == SourceStatus.NO_PROFILE
    assert s.sql("SELECT COUNT(*) FROM output_settings") == [(1,)]
    await s.counted(lambda: s.catalog.update_source(source_id, parser_profile_id=7))
    assert len((await s.get(series_id)).rows) == 17


@pytest.mark.asyncio
async def test_a_command_the_registry_refuses_is_refused_with_its_reason_and_stores_nothing(system):
    s = system
    series_id = await laid_out(s)
    for lever, value, target, code in [("no.such", True, None, "unknown_lever"),
                                       ("variant.allow", True, None, "bad_target"),
                                       ("voice.take", "yes", group_target("Рутина"), "bad_value")]:
        with pytest.raises(BusRequestError) as caught:
            await s.set(series_id, lever, value, season=1, target=target)
        assert code_of(caught.value) == code
    with pytest.raises(BusRequestError) as caught:
        await s.reset(series_id, season=1, lever="variant.allow")
    assert code_of(caught.value) == "bad_target"
    with pytest.raises(BusRequestError) as caught:
        await s.set(999, "voice.take", True, target=group_target("Рутина"))
    assert "LookupError" in str(caught.value)
    assert s.sql("SELECT COUNT(*) FROM output_settings") == [(0,)] and s.taken(s.changed) == []


# --- M-34: a lever and a consumer added through the registries alone -------------------------------------------------

class Consumer(BaseModule):
    """A consumer of the layout as any module is: it hears `composition.changed` and asks `composition.get`."""

    name = "consumer"

    def __init__(self, bus) -> None:
        self.seen: asyncio.Queue = asyncio.Queue()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("composition.changed", self.on_changed)

    async def on_changed(self, env) -> None:
        reply = await self.request("composition.get", {"series_id": env.payload["series_id"]}, timeout=STUCK_S)
        self.seen.put_nowait((env.payload, CompositionReply.model_validate(reply)))


@pytest.mark.asyncio
async def test_a_consumer_and_a_lever_that_are_only_registered_work_with_the_module_unchanged(system):
    s = system
    consumer = Consumer(s.bus)
    await consumer.start()
    levers.register(Lever("fake.mute", TargetKind.GROUP, ValueShape.FLAG, False, "voices"))
    try:
        series_id = await laid_out(s)
        for _ in range(2):
            await asyncio.wait_for(consumer.seen.get(), STUCK_S)
        await s.set(series_id, "fake.mute", True, season=2, target=group_target("Рутина"))
        event, reply = await asyncio.wait_for(consumer.seen.get(), STUCK_S)
        assert event["episodes"] == refs((2, 1), (2, 2))
        at = levers_by_episode(reply)
        assert at[2, 1]["fake.mute"] == EffectiveLever(lever="fake.mute", target=group_target("Рутина"), value=True,
                                                       level=Level.SEASON)
        assert "fake.mute" not in at[1, 1]
        assert any(each.name == "fake.mute" for each in reply.levers)  # a window is given the lever to draw
        with pytest.raises(BusRequestError) as caught:
            await s.set(series_id, "fake.mute", 101, season=2, target=group_target("Рутина"))
        assert code_of(caught.value) == "bad_value"
    finally:
        levers.unregister("fake.mute")
        await consumer.stop()
    assert "fake.mute" not in {x.lever for e in (await s.get(series_id)).settings for x in e.levers}


# --- C-1, C-2: a cause that comes while the series is being counted --------------------------------------------------

@pytest.mark.asyncio
async def test_a_newer_table_that_comes_during_a_count_is_counted_after_it_and_the_older_layout_is_never_after_the_newer(
        system):
    """C-1."""
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    rev = (await s.get(series_id)).rev
    s.rules.hold()
    s.rules.profiles[7] = lambda text: {**lecturer(text), "part": 1}
    s.profile_changed()
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)

    s.sources.finish(PAGE, page(*season_rows(1, range(1, 5))))
    s.rules.release()
    first, second = await s.next(s.changed), await s.next(s.changed)
    await s.composition.idle()
    assert (first["rev"], second["rev"]) == (rev + 1, rev + 2)
    assert first["episodes"] == season_one(1, 2) and second["episodes"] == season_one(3, 4)
    final = await s.get(series_id)
    assert [(r.episode_start, r.part) for r in final.rows] == [(1, 1), (2, 1), (3, 1), (4, 1)]
    assert final.rev == rev + 2 and s.taken(s.changed) == []


@pytest.mark.asyncio
async def test_a_cause_that_comes_during_a_count_sets_a_flag_and_the_series_is_counted_once_more(system):
    """C-2."""
    s = system
    series_id = await laid_out(s)
    s.rules.hold()
    s.composition._schedule(series_id)
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)
    for _ in range(3):
        s.composition._schedule(series_id)
    assert len(s.composition._flights) == 1 and s.composition._flights[series_id].dirty
    while not s.catalog.gets.empty():
        s.catalog.gets.get_nowait()
    s.rules.release()
    await s.composition.idle()
    assert s.catalog.gets.qsize() == 1 and not s.composition._flights


@pytest.mark.asyncio
async def test_a_series_that_is_held_does_not_hold_another(system):
    s = system
    held = await laid_out(s)
    s.rules.hold()
    s.composition._schedule(held)
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)
    other = s.catalog.add_series([source(OTHER_PAGE, parser_profile_id=None)])
    while (await s.next(s.changed))["series_id"] != other:
        pass
    assert (await s.get(other)).sources[0].status == SourceStatus.WAITING and held in s.composition._flights
    s.rules.release()
    await s.composition.idle()


# --- a series that is deleted -----------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_series_that_is_deleted_during_a_count_is_taken_whole_after_it_and_reported(system):
    s = system
    series_id = await laid_out(s)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Рутина"))
    s.taken(s.changed)
    s.rules.hold()
    s.rules.profiles[7] = lambda text: {**lecturer(text), "part": 1}  # a count that would change the layout
    s.profile_changed()
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)
    s.catalog.delete_series(series_id)
    s.rules.release()
    assert await s.next(s.purged) == {"series_id": series_id, "owner": "composition"}
    for table in ("composition_rows", "composition_state", "composition_sources", "unknown_voices", "output_settings"):
        assert s.sql(f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert s.taken(s.changed) == [] and s.taken(s.failed) == []
    assert (await s.get(series_id)).rev == 0


# --- C-9: two series on one page ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_series_on_one_page_share_one_read_and_each_has_its_own_layout(system):
    s = system
    s.rules.profiles[8] = lambda text: {**lecturer(text), "episode": lecturer(text)["episode"] + 100}
    first = await s.counted(lambda: s.catalog.add_series([source(PAGE, parser_profile_id=7)]))
    second = await s.counted(lambda: s.catalog.add_series([source(PAGE, parser_profile_id=8)]))
    assert s.sources.reads_begun == [PAGE]
    s.taken(s.changed)
    s.sources.finish(PAGE, page(*season_rows(1, range(1, 3))))
    heard = {(await s.next(s.changed))["series_id"] for _ in range(2)}
    await s.composition.idle()
    assert heard == {first, second}
    assert [r.episode_start for r in (await s.get(first)).rows] == [1, 2]
    assert [r.episode_start for r in (await s.get(second)).rows] == [101, 102]


# --- C-10: a read that failed ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_read_that_failed_leaves_the_layout_and_names_the_reason_until_the_page_is_read(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    before = await s.get(series_id)
    why = reason("read_failed")
    await s.counted(lambda: s.sources.fail(PAGE, why))
    [event] = s.taken(s.changed)
    failed = await s.get(series_id)
    assert event["rev"] == before.rev + 1 and event["summary_only"] is True and event["episodes"] == []
    assert failed.rows == before.rows
    assert (failed.sources[0].status, failed.sources[0].reason) == (SourceStatus.FAILED, why)
    assert s.sources.requests[-1].refresh is False and s.sources.reads_begun == [PAGE]  # the verdict is read, not retried

    later = "2026-11-01T13:00:00+00:00"
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 3))), read_at=later))
    [event] = s.taken(s.changed)
    healed = await s.get(series_id)
    assert event["summary_only"] is True and event["rev"] == before.rev + 2
    assert (healed.sources[0].status, healed.sources[0].reason, healed.sources[0].read_at) == (
        SourceStatus.READY, None, later)
    assert healed.rows == before.rows


@pytest.mark.asyncio
async def test_a_new_read_begun_for_a_source_forgets_the_verdict_of_the_one_before(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source()]))
    await s.counted(lambda: s.sources.fail(PAGE, reason("read_failed")))
    assert (await s.get(series_id)).sources[0].status == SourceStatus.FAILED
    await s.counted(lambda: s.catalog.update_source(only_source(s, series_id), vk_search_mode="get_all"))
    assert (await s.get(series_id)).sources[0].status == SourceStatus.WAITING
    assert s.sources.requests[-1].options == {"vk_search_mode": "get_all"} and s.sources.requests[-1].refresh
    await s.counted(lambda: s.voices.set_groups({}))
    assert (await s.get(series_id)).sources[0].status == SourceStatus.WAITING


@pytest.mark.asyncio
async def test_a_read_that_failed_is_not_followed_by_another_read_of_a_recount_whatever_counts_the_series_again(system):
    """The loop read, fail, read: a failed page is `failed`, not `absent`, so a recount never begins a read of it."""
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source()]))
    await s.counted(lambda: s.sources.fail(PAGE, reason("read_failed")))
    await s.counted(lambda: s.voices.set_groups({}))
    await s.counted(s.profile_changed)
    await s.counted(lambda: s.catalog.update_source(only_source(s, series_id)))  # asks for a read: a fresh table is none
    assert s.sources.reads_begun == [PAGE, PAGE]
    await s.counted(lambda: s.sources.fail(PAGE, reason("read_failed")))
    await s.counted(lambda: s.voices.set_groups({}))
    assert s.sources.reads_begun == [PAGE, PAGE]
    assert (await s.get(series_id)).sources[0].status == SourceStatus.FAILED


@pytest.mark.asyncio
async def test_a_page_nothing_was_ever_read_of_is_read_once_when_the_series_is_counted_again(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source()]))
    s.sources.reading.clear()  # the sources module came back with nothing stored of the page
    await s.composition.stop()
    again = CompositionModule(s.bus, Database(str(s.db_path)))
    await again.start()
    try:
        await again.idle()
        assert s.sources.reads_begun == [PAGE, PAGE]
        assert [request.refresh for request in s.sources.requests[-2:]] == [False, True]
        assert (await s.get(series_id)).sources[0].status == SourceStatus.WAITING
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_an_address_no_site_reads_is_a_failed_source_and_does_not_stop_the_others(system):
    s = system
    series_id = await s.counted(lambda: s.catalog.add_series([source("https://nowhere.test/x"), source(PAGE)]))
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 3)))))
    reply = await s.get(series_id)
    assert [each.status for each in reply.sources] == [SourceStatus.FAILED, SourceStatus.READY]
    assert parse(reply.sources[0].reason)["code"] == "site_not_found" and len(reply.rows) == 2


# --- a neighbour that does not answer -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_neighbour_that_does_not_answer_fails_the_count_and_the_previous_layout_stays(system):
    s = system
    series_id = await laid_out(s)
    before = await s.get(series_id)
    s.rules.broken = True
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["Ruchime"]}))
    [failure] = s.taken(s.failed)
    assert failure["series_id"] == series_id
    assert parse(failure["reason"]) == {"code": "composition_request_failed", "params": {"topic": "rules.apply"}}
    assert await s.get(series_id) == before and s.taken(s.changed) == []

    s.rules.broken = False
    await s.counted(lambda: s.voices.set_groups({"Рутина": ["Ruchime"]}))
    assert {r.voice_group for r in (await s.get(series_id)).rows} == {"Рутина"} and s.taken(s.failed) == []


@pytest.mark.asyncio
async def test_an_answer_that_is_not_the_contract_is_a_failed_count_too(system):
    s = system
    series_id = await laid_out(s)
    before = await s.get(series_id)
    s.voices.garbage = True
    await s.counted(lambda: s.voices.set_groups({}))
    [failure] = s.taken(s.failed)
    assert parse(failure["reason"])["params"] == {"topic": "voices.groups.resolve"}
    assert await s.get(series_id) == before


# --- C-8, I-21: the module stops and starts ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_count_that_a_stop_cut_short_is_made_again_at_the_next_start_from_the_database(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    rev = (await s.get(series_id)).rev
    s.rules.hold()
    s.voices.set_groups({"Рутина": ["Ruchime"]})
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)
    await s.composition.stop()
    s.rules.release()
    s.sources.finish(PAGE, page(*season_rows(1, range(1, 4))))

    again = CompositionModule(s.bus, Database(str(s.db_path)))
    await again.start()
    try:
        event = await s.next(s.changed)
        await again.idle()
        assert event["series_id"] == series_id and event["rev"] == rev + 1
        rows = (await s.get(series_id)).rows
        assert [r.episode_start for r in rows] == [1, 2, 3] and {r.voice_group for r in rows} == {"Рутина"}
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_restart_with_nothing_changed_counts_everything_again_and_publishes_nothing(system):
    s = system
    series_id = await laid_out(s)
    await s.set(series_id, "voice.take", False, season=1, target=group_target("Рутина"))
    before = await s.get(series_id)
    s.taken(s.changed)
    await s.composition.stop()
    again = CompositionModule(s.bus, Database(str(s.db_path)))
    await again.start()
    try:
        await again.idle()
        assert s.taken(s.changed) == [] and s.taken(s.failed) == []
        assert await s.get(series_id) == before
        assert s.sources.requests[-1].refresh is False
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_what_the_database_keeps_of_a_series_the_catalog_no_longer_has_is_taken_at_start(system, db_path):
    s = system
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("INSERT INTO composition_state (series_id, rev) VALUES (42, 3)")
        conn.execute("INSERT INTO unknown_voices (series_id, name_key, number) VALUES (42, 'x', 1)")
        conn.execute("INSERT INTO output_settings (series_id, lever, value) VALUES (42, 'reference.choice', 'null')")
        conn.commit()
    again = CompositionModule(s.bus, Database(str(db_path)))
    await again.start()
    try:
        await again.idle()
        assert s.sql("SELECT COUNT(*) FROM composition_state") == [(0,)]
        assert s.sql("SELECT COUNT(*) FROM output_settings") == [(0,)] and s.taken(s.failed) == []
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_start_that_finds_no_catalog_still_starts(db_path):
    module = CompositionModule(Bus(), Database(str(db_path)))
    await module.start()
    try:
        assert module._flights == {}
    finally:
        await module.stop()


# --- the race of an event ahead of the answer it belongs to ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_page_that_is_read_the_moment_after_the_answer_that_began_the_read_is_not_missed(system):
    s = system
    s.sources.finish_at_once[PAGE] = standard()
    series_id = await s.counted(lambda: s.catalog.add_series([source()]))
    while len((await s.get(series_id)).rows) != 7:
        await s.next(s.changed)
        await s.composition.idle()
    assert (await s.get(series_id)).sources[0].status == SourceStatus.READY


@pytest.mark.asyncio
async def test_an_event_of_a_page_that_overtakes_the_answer_that_names_it_makes_the_count_run_again(system):
    s = system
    s.sources.hold()
    heard = asyncio.Event()
    wake = s.composition._wake
    s.composition._wake = lambda url: (wake(url), heard.set())
    series_id = s.catalog.add_series([source()])
    await asyncio.wait_for(s.sources.asked.get(), STUCK_S)

    s.sources.finish(PAGE, standard())
    await asyncio.wait_for(heard.wait(), STUCK_S)
    s.sources.release()
    waiting, laid = await s.next(s.changed), await s.next(s.changed)
    await s.composition.idle()
    assert (waiting["rev"], laid["rev"]) == (1, 2)
    assert len((await s.get(series_id)).rows) == 7


# --- the tables --------------------------------------------------------------------------------------------------------

def test_the_migration_makes_the_tables_and_takes_them_away(tmp_path):
    path = tmp_path / "m.db"
    migrate(path, "upgrade")
    expected = {"composition_rows", "composition_state", "composition_sources",
                "unknown_voices", "output_settings"}
    engine = create_engine(f"sqlite:///{path}")
    assert expected <= set(inspect(engine).get_table_names())
    engine.dispose()
    migrate(path, "downgrade")
    engine = create_engine(f"sqlite:///{path}")
    assert not expected & set(inspect(engine).get_table_names())
    engine.dispose()


def test_a_level_of_a_lever_is_kept_once_whether_or_not_it_has_a_season_or_an_episode(tmp_path):
    path = tmp_path / "m.db"
    migrate(path, "upgrade")
    insert = ("INSERT INTO output_settings (series_id, season, episode, lever, target, value)"
              " VALUES (?, ?, ?, ?, ?, 'true')")
    levels = [(1, None, None, "voice.take", "group:a"), (1, 1, None, "voice.take", "group:a"),
              (1, 1, 2, "voice.take", "group:a"), (1, None, None, "reference.choice", None),
              (2, None, None, "reference.choice", None)]
    with closing(sqlite3.connect(path)) as conn:
        for level in levels:
            conn.execute(insert, level)
        for again in levels[:4]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(insert, again)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (1, None, 2, "voice.take", "group:b"))


# --- the time of a reading is no change ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_page_read_again_with_the_same_rows_at_another_time_is_counted_without_a_publication(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 4))))
    before = await s.get(series_id)
    later = "2026-11-01T13:00:00+00:00"
    await s.counted(lambda: s.sources.finish(PAGE, page(*season_rows(1, range(1, 4))), read_at=later))
    assert s.taken(s.changed) == []
    after = await s.get(series_id)
    assert (after.rev, after.rows) == (before.rev, before.rows)
    assert before.sources[0].read_at != later and after.sources[0].read_at == later  # kept, but no news


# --- the headers of the levels ---------------------------------------------------------------------------------------------

def headers(reply: CompositionReply) -> dict:
    return {level.season: {x.lever: (x.value, x.level) for x in level.levers} for level in reply.levels}


@pytest.mark.asyncio
async def test_the_series_and_each_season_show_their_own_levers_and_what_they_inherit(system):
    s = system
    series_id = await laid_out(s)
    assert [level.season for level in (await s.get(series_id)).levels] == [None, 1, 2]
    await s.set(series_id, "voice.take", False, target=group_target("Рутина"))
    await s.set(series_id, "voice.take", True, season=1, target=group_target("Рутина"))
    await s.set(series_id, "reference.choice", choice(only_source(s, series_id)), season=2)
    shown = headers(await s.get(series_id))
    assert shown[None]["voice.take"] == (False, Level.SERIES) and shown[None]["reference.choice"] == (None, Level.DEFAULT)
    assert shown[1]["voice.take"] == (True, Level.SEASON) and shown[1]["reference.choice"] == (None, Level.DEFAULT)
    assert shown[2]["voice.take"] == (False, Level.SERIES)
    assert shown[2]["reference.choice"] == (choice(only_source(s, series_id)), Level.SEASON)


@pytest.mark.asyncio
async def test_an_episode_with_a_value_of_its_own_leaves_the_headers_as_they_are(system):
    s = system
    series_id = await laid_out(s, rich_standard())
    await s.set(series_id, "reference.choice", choice(only_source(s, series_id)), season=1)
    before = (await s.get(series_id)).levels
    await s.set(series_id, "reference.choice", choice(only_source(s, series_id), "kodik"), season=1, episode=3)
    await s.set(series_id, "voice.take", False, season=1, episode=3, target=group_target("Рутина"))
    after = await s.get(series_id)
    assert after.levels == before
    assert levers_by_episode(after)[1, 3]["reference.choice"].level == Level.EPISODE


@pytest.mark.asyncio
async def test_a_header_that_no_episode_follows_changes_on_the_screen_and_is_told_to_windows_alone(system):
    s = system
    series_id = await laid_out(s)
    for episode in (1, 2):
        await s.set(series_id, "voice.take", False, season=2, episode=episode, target=group_target("Рутина"))
    s.taken(s.changed)
    rev = (await s.get(series_id)).rev
    await s.set(series_id, "voice.take", True, season=2, target=group_target("Рутина"))
    assert s.taken(s.changed) == [{"series_id": series_id, "rev": rev + 1, "episodes": [], "summary_only": True}]
    assert headers(await s.get(series_id))[2]["voice.take"] == (True, Level.SEASON)


@pytest.mark.asyncio
async def test_a_setting_that_no_episode_and_no_header_shows_is_kept_and_told_to_nobody(system):
    s = system
    series_id = await laid_out(s)
    rev = (await s.get(series_id)).rev
    await s.set(series_id, "voice.take", False, season=3, episode=1, target=group_target("Рутина"))
    assert s.taken(s.changed) == [] and (await s.get(series_id)).rev == rev
    assert s.sql("SELECT season, episode FROM output_settings WHERE series_id = ?", series_id) == [(3, 1)]


# --- composition.recompute -----------------------------------------------------------------------------------------------------

async def recompute(s: System, series_id: int) -> dict:
    return await s.probe.request("composition.recompute", {"series_id": series_id}, timeout=STUCK_S)


@pytest.mark.asyncio
async def test_a_recount_asked_for_is_answered_after_the_count_is_written_and_told(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    rev = (await s.get(series_id)).rev
    s.rules.profiles[7] = lambda text: {**lecturer(text), "part": 1}  # no event announces it: only the request counts
    assert await recompute(s, series_id) == {"rev": rev + 1, "changed": True}
    [event] = s.taken(s.changed)  # the event was out before the answer
    assert (event["rev"], event["summary_only"]) == (rev + 1, False)
    reply = await s.get(series_id)
    assert reply.rev == rev + 1 and {r.part for r in reply.rows} == {1}
    assert s.sources.requests[-1].refresh is False and not s.sources.reads_begun[1:]


@pytest.mark.asyncio
async def test_a_recount_asked_for_with_the_same_data_changes_nothing_and_says_so(system):
    s = system
    series_id = await laid_out(s)
    before = await s.get(series_id)
    assert await recompute(s, series_id) == {"rev": before.rev, "changed": False}
    assert await recompute(s, series_id) == {"rev": before.rev, "changed": False}
    assert s.taken(s.changed) == [] and (await s.get(series_id)).rev == before.rev


@pytest.mark.asyncio
async def test_a_request_during_a_count_is_answered_by_the_count_that_begins_after_it(system):
    s = system
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    rev = (await s.get(series_id)).rev
    s.rules.hold()
    s.composition._schedule(series_id)
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)  # a count is running, it has read the table of two episodes

    asked = asyncio.Event()
    schedule = s.composition._schedule
    s.composition._schedule = lambda *args, **more: (schedule(*args, **more), asked.set())
    s.sources.tables[PAGE] = page(*season_rows(1, range(1, 5)), url=PAGE).model_copy(
        update={"complete": True, "read_at": "2026-11-01T13:00:00+00:00"})
    answer = asyncio.create_task(recompute(s, series_id))
    await asyncio.wait_for(asked.wait(), STUCK_S)
    s.rules.release()
    assert await asyncio.wait_for(answer, STUCK_S) == {"rev": rev + 1, "changed": True}
    assert [r.episode_start for r in (await s.get(series_id)).rows] == [1, 2, 3, 4]
    assert s.taken(s.changed)[-1]["rev"] == rev + 1


@pytest.mark.asyncio
async def test_a_neighbour_that_does_not_answer_refuses_the_request_with_the_reason_and_the_failure_is_told_too(system):
    s = system
    series_id = await laid_out(s)
    s.rules.broken = True
    with pytest.raises(BusRequestError) as caught:
        await recompute(s, series_id)
    assert parse(str(caught.value).split(": ", 1)[1]) == {
        "code": "composition_request_failed", "params": {"topic": "rules.apply"}}
    [failure] = s.taken(s.failed)
    assert failure["series_id"] == series_id and parse(failure["reason"])["params"] == {"topic": "rules.apply"}
    assert s.taken(s.changed) == []


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


@pytest.mark.asyncio
async def test_a_series_deleted_while_a_request_waits_refuses_the_request(system):
    s = system
    series_id = await laid_out(s)
    s.rules.hold()
    s.composition._schedule(series_id)
    await asyncio.wait_for(s.rules.entered.get(), STUCK_S)
    answer = asyncio.create_task(recompute(s, series_id))
    s.catalog.delete_series(series_id)
    s.rules.release()
    with pytest.raises(BusRequestError) as caught:
        await asyncio.wait_for(answer, STUCK_S)
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
async def test_an_address_without_a_profile_shows_the_summary_of_its_page_and_the_lines_of_the_constructor(system):
    s = system
    s.sources.tables[PAGE] = standard().model_copy(
        update={"url": PAGE, "complete": True, "read_at": "2026-11-01T12:00:00+00:00"})
    reply = await preview(s)
    assert (reply.url, reply.state, reply.reason) == (PAGE, TableState.READY, None)
    summary = reply.summary
    assert (summary.source_id, summary.url, summary.status, summary.read_at) == (
        0, PAGE, SourceStatus.NO_PROFILE, "2026-11-01T12:00:00+00:00")
    [player] = summary.players
    assert (player.player, player.rows, player.groups) == ("vk", 7, 2)
    assert reply.rows == [] and s.voices.asked == []
    assert len(reply.lines) == 7 and all(each.result is None for each in reply.lines)
    assert (reply.lines[0].key, reply.lines[0].line) == ("vk-1-1", "PID:fake.test | P:VK | Асура | Серия 1 | G:Сезон 1 | V:Ruchime | R:1080p")
    assert all(each.heights == [720, 1080] for each in reply.lines)
    assert s.rules.calls == [] and s.sources.requests[-1].refresh is True
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_a_line_carries_the_resolutions_of_its_row_only_while_the_row_can_be_played(system):
    s = system
    s.sources.tables[PAGE] = page(
        row("a", "Серия 1"), row("b", "Серия 2", heights=[4320], state=RowState.ABSENT),
    ).model_copy(update={"url": PAGE, "complete": True})
    reply = await preview(s)
    assert [(each.key, each.heights) for each in reply.lines] == [("a", [720, 1080]), ("b", [])]


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
    assert (reply.state, reply.summary, reply.names) == (TableState.READY, None, [])
    assert [(each.key, each.line) for each in reply.lines] == [("0", "Серия 1 [Мой дубляж]"), ("1", "Трейлер")]
    assert [each.heights for each in reply.lines] == [[], []]
    [call] = s.rules.calls
    assert (call.profile_id, call.series_season, [(l.key, l.text) for l in call.lines]) == (
        7, "4", [("0", "Серия 1 [Мой дубляж]"), ("1", "Трейлер")])
    assert s.sources.requests == [] and s.sources.reads_begun == []
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_lines_without_a_profile_come_back_without_an_answer_and_nothing_is_asked(system):
    s = system
    reply = await s.probe.request("composition.preview", {"lines": ["Серия 1"]}, timeout=STUCK_S)
    reply = CompositionPreviewReply.model_validate(reply)
    assert [(each.line, each.result) for each in reply.lines] == [("Серия 1", None)]
    assert s.rules.calls == [] and s.sources.requests == []
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_an_address_whose_page_is_being_read_for_the_first_time_has_a_state_and_nothing_else(system):
    s = system
    reply = await preview(s)
    assert (reply.state, reply.summary, reply.lines) == (TableState.READING, None, [])
    assert s.sources.reads_begun == [PAGE] and s.rules.calls == []
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_a_page_read_again_shows_its_last_table_while_it_is_read(system):
    s = system
    s.sources.tables[PAGE] = standard().model_copy(update={"url": PAGE, "complete": True})
    s.sources.stale = True
    reply = await preview(s, profile_id=7)
    assert reply.state == TableState.READING and reply.summary.status == SourceStatus.READY and len(reply.lines) == 7
    assert s.sources.reads_begun == [PAGE]
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_a_page_whose_last_read_failed_shows_the_reason_and_the_table_it_had(system):
    s = system
    s.sources.finish(PAGE, standard())
    why = reason("read_failed")
    s.sources.fail(PAGE, why)
    reply = await preview(s, refresh=False)
    assert (reply.state, reply.reason) == (TableState.FAILED, why)
    assert (reply.summary.status, reply.summary.reason) == (SourceStatus.FAILED, why)
    assert len(reply.lines) == 7 and reply.summary.players[0].rows == 7
    assert s.sources.reads_begun == []
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_a_request_that_asks_for_no_read_of_a_page_nothing_was_read_of_gives_the_state_alone(system):
    s = system
    reply = await preview(s, refresh=False)
    assert (reply.state, reply.reason, reply.summary, reply.lines) == (TableState.ABSENT, None, None, [])
    assert s.sources.reads_begun == [] and s.sources.requests[-1].refresh is False
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_an_address_no_site_reads_is_a_failed_state_with_the_reason_and_no_summary(system):
    s = system
    reply = await preview(s, "https://nowhere.test/x")
    assert (reply.state, reply.summary, reply.lines) == (TableState.FAILED, None, [])
    assert parse(reply.reason)["code"] == "site_not_found"
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_the_options_of_the_request_go_to_the_sources_as_they_are(system):
    s = system
    await preview(s, options={"vk_search_mode": "get_all"}, refresh=False)
    assert s.sources.requests[-1].options == {"vk_search_mode": "get_all"}


@pytest.mark.asyncio
async def test_a_neighbour_that_does_not_answer_refuses_the_preview_and_nothing_is_written_or_told(system):
    s = system
    s.sources.tables[PAGE] = standard().model_copy(update={"url": PAGE, "complete": True})
    s.rules.broken = True
    with pytest.raises(BusRequestError) as caught:
        await preview(s, profile_id=7)
    assert parse(str(caught.value).split(": ", 1)[1]) == {
        "code": "composition_request_failed", "params": {"topic": "rules.apply"}}
    nothing_was_written(s)


@pytest.mark.asyncio
async def test_the_preview_of_a_page_a_series_reads_leaves_the_series_alone(system):
    s = system
    series_id = await laid_out(s)
    before = await s.get(series_id)
    reply = await preview(s, profile_id=7, refresh=False)
    assert reply.summary.status == SourceStatus.READY and len(reply.lines) == 7
    assert await s.get(series_id) == before and s.taken(s.changed) == [] and not s.composition._flights


@pytest.mark.asyncio
async def test_the_summary_names_the_groups_of_a_player_and_the_canonical_address_of_its_page(system):
    s = system
    series_id = await laid_out(s)
    summary = (await s.get(series_id)).sources[0]
    assert summary.canonical_url == PAGE
    assert all(player.group_names == list(dict.fromkeys(player.group_names)) for player in summary.players)
    assert sum(len(player.group_names) for player in summary.players) == sum(player.groups for player in summary.players)


@pytest.mark.asyncio
async def test_a_request_for_the_sources_only_gives_the_summaries_and_no_rows_levels_or_settings(system):
    s = system
    series_id = await laid_out(s)
    full = await s.get(series_id)
    light = await s.get(series_id, sources_only=True)
    assert light.sources == full.sources and light.rev == full.rev
    assert light.rows == [] and light.levels == [] and light.settings == []
    assert full.rows and full.levels and full.settings


@pytest.mark.asyncio
async def test_the_preview_gives_the_names_of_the_release_as_the_table_has_them(system):
    s = system
    await laid_out(s)
    reply = await preview(s, profile_id=None, refresh=False)
    assert reply.names == list(standard().names) and reply.names



# --- what may be picked: the table of what is allowed in each state of a variant ---------------------------------------

@pytest.mark.asyncio
async def test_a_variant_without_a_line_that_plays_is_refused_as_the_reference_and_as_the_preferred_one(system):
    s = system
    series_id = await laid_out(s, page(row("a", "Серия 1"), row("b", "Серия 2", player="kodik", state=RowState.ABSENT)))
    source_id = only_source(s, series_id)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "voice.player", {"source_id": source_id, "player": "kodik"}, target=group_target(choice(1)["group"]))
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "reference.choice", choice(source_id, "allvideo"), season=1)  # no line of it at all
    assert s.sql("SELECT lever FROM output_settings WHERE series_id = ?", series_id) == []
    assert await s.set(series_id, "reference.choice", choice(source_id), season=1) == {"ok": True}


@pytest.mark.asyncio
async def test_a_variant_of_compilations_only_may_be_preferred_and_not_made_the_reference(system):
    s = system
    series_id = await laid_out(s, page(row("a", "Серия 1"), row("c", "2-4 серия", player="kodik")))
    source_id = only_source(s, series_id)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1)
    assert await s.set(series_id, "voice.player", {"source_id": source_id, "player": "kodik"},
                       target=group_target(choice(source_id)["group"])) == {"ok": True}


@pytest.mark.asyncio
async def test_the_layout_carries_the_table_of_what_is_allowed(system):
    s = system
    series_id = await laid_out(s)
    from core.contracts import availability
    assert (await s.get(series_id)).availability == availability.table()


@pytest.mark.asyncio
async def test_a_variant_is_judged_by_the_lines_of_the_level_it_is_set_for_as_the_window_judges_it(system):
    """The variant has a line that plays in episode 1 and none in episode 2: it may be the reference of the season and of
    episode 1, and is refused as the reference of episode 2, whatever the other episodes have."""
    s = system
    series_id = await laid_out(s, page(row("a", "Серия 1"), row("b", "Серия 2"), row("c", "Серия 1", player="kodik")))
    source_id = only_source(s, series_id)
    assert await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=1) == {"ok": True}
    assert await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1) == {"ok": True}
    assert await s.set(series_id, "reference.choice", choice(source_id, "kodik")) == {"ok": True}
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=1, episode=2)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "voice.player", {"source_id": source_id, "player": "kodik"},
                    target=group_target(choice(source_id)["group"]), season=1, episode=2)
    with pytest.raises(BusRequestError, match="not_allowed"):  # a season that has no line of the variant
        await s.set(series_id, "reference.choice", choice(source_id, "kodik"), season=2)


# --- a sync of the series is one run: its layout is the snapshot, its levers are not changed (ЭТАП_11) ---------------

@pytest.mark.asyncio
async def test_a_series_whose_sync_is_under_way_keeps_its_layout_and_refuses_its_levers_until_the_run_ends(system):
    from core.contracts import RUN_CHANGED
    s = system
    s.rules.profiles[7] = lambda text: {key: value for key, value in lecturer(text).items() if key != "season"}
    series_id = await laid_out(s, page(*season_rows(1, range(1, 3))))
    before = await s.get(series_id)
    s.probe.publish_event(RUN_CHANGED, {"series_id": series_id, "active": True})
    await s.bus.settled()
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.set(series_id, "voice.take", False, target=group_target("Ruchime"))
    with pytest.raises(BusRequestError, match="not_allowed"):
        await s.reset(series_id)

    s.catalog.set_season(series_id, "2")  # an event that would count the layout again
    s.profile_changed()
    await s.bus.settled()
    held = await s.get(series_id)
    assert held.rev == before.rev and {r.season for r in held.rows} == {1}  # the snapshot stands

    reply = await s.probe.request("composition.recompute", {"series_id": series_id}, timeout=STUCK_S)
    assert reply["rev"] >= before.rev  # the count the sync itself asks for is done
    await s.counted(lambda: s.probe.publish_event(RUN_CHANGED, {"series_id": series_id, "active": False}))
    await s.bus.settled()
    assert {r.season for r in (await s.get(series_id)).rows} == {2}  # what was owed is counted once the run is over
