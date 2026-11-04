"""The constructor over the bus (stage 10, П-2): rules.apply by key, rules.profile.changed, file names from
ready-made numbers, the profile of a torrent file from the request, and the deletion of a profile.
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.contracts import RulesApplyReply, RulesProfileChanged
from core.db import Database
from core.reasons import parse
from modules.rules import RulesModule
from series_factory import insert_series, source

SITE = "https://dunhuatv.ru/title/x"


def alembic(path, *args):
    subprocess.run([sys.executable, "-m", "alembic", *args],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    alembic(path, "upgrade", "head")
    return str(path)


class Probe(BaseModule):
    name = "probe"


class CatalogStub(BaseModule):
    """Answers the one question the constructor asks the catalog: which sources read a profile."""
    name = "catalog"

    def __init__(self, bus):
        self.readers: dict[int, list[dict]] = {}
        # Answers given one per question before `readers` takes over: a source may appear between two questions.
        self.script: list[list[dict]] = []
        self.asked: list[int] = []
        super().__init__(bus)

    def register(self):
        self.handle("catalog.sources.by_profile", self.on_by_profile)

    async def on_by_profile(self, env):
        self.asked.append(env.payload["profile_id"])
        if self.script:
            return self.script.pop(0)
        return self.readers.get(env.payload["profile_id"], [])


class Recounter(BaseModule):
    """What the layout does on the event: asks the constructor again at once, and says when it has the answer."""
    name = "recounter"

    def __init__(self, bus):
        self.answers: list[RulesApplyReply] = []
        self.answered = asyncio.Event()
        super().__init__(bus)

    def register(self):
        self.handle("rules.profile.changed", self.on_changed)

    async def on_changed(self, env):
        change = RulesProfileChanged.model_validate(env.payload)
        reply = await self.request("rules.apply", {
            "profile_id": change.profile_id, "lines": [{"key": "k", "text": "серия 4"}]}, timeout=5)
        self.answers.append(RulesApplyReply.model_validate(reply))
        self.answered.set()


async def start_system(db_path, *, catalog: bool = True, layout: bool = False):
    bus = Bus()
    probe, rules = Probe(bus), RulesModule(bus, Database(db_path))
    stub = CatalogStub(bus) if catalog else None
    recounter = Recounter(bus) if layout else None
    runner = Runner(bus, [m for m in (rules, stub, recounter, probe) if m is not None])
    await runner.start()
    return runner, SimpleNamespace(bus=bus, probe=probe, rules=rules, catalog=stub, recounter=recounter,
                                   db_path=db_path)


@pytest.fixture
async def system(db_path):
    runner, found = await start_system(db_path)
    yield found
    await runner.stop()


@pytest.fixture
async def system_with_layout(db_path):
    runner, found = await start_system(db_path, layout=True)
    yield found
    await runner.stop()


@pytest.fixture
async def system_without_catalog(db_path):
    runner, found = await start_system(db_path, catalog=False)
    yield found
    await runner.stop()


def rows(db_path, query, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(query, params).fetchall()


def drain(sub) -> list:
    """What the subscription holds now; a handler publishes before it replies, so nothing is awaited."""
    found = []
    while not sub.queue.empty():
        env = sub.queue.get_nowait()
        found.append((env.topic, env.payload))
    return found


def code_of(exc: BusRequestError) -> dict:
    """The reason of a refusal that travelled over the bus as `ValueError: {json}`."""
    return parse(str(exc).split(": ", 1)[1])


# --- patterns of rules ------------------------------------------------------------------------

def blocks_of(*specs) -> str:
    """'N' is a number, ('add', '10') a modifier, any other string a text."""
    out = []
    for spec in specs:
        if spec == "N":
            out.append({"type": "number"})
        elif isinstance(spec, tuple):
            out.append({"type": spec[0], "value": spec[1]})
        else:
            out.append({"type": "text", "value": spec})
    return json.dumps(out, ensure_ascii=False)


def extract(action_type, *specs) -> dict:
    return {"action_type": action_type, "action_pattern": blocks_of(*specs)}


def assign(action_type, value) -> dict:
    return {"action_type": action_type, "action_pattern": value}


def contains(text) -> dict:
    return {"condition_type": "contains", "logical_operator": "AND", "pattern": blocks_of(text)}


def rule_data(name, actions, conditions=(), keep_going=False) -> dict:
    return {"name": name, "action_pattern": json.dumps(actions, ensure_ascii=False),
            "continue_after_match": keep_going, "conditions": list(conditions)}


async def new_profile(probe, name="профиль") -> int:
    return (await probe.request("rules.profiles.create", {"name": name}, timeout=5))["id"]


async def new_rule(probe, profile_id, name, actions, conditions=(), keep_going=False) -> int:
    reply = await probe.request("rules.rules.add", {
        "profile_id": profile_id, "data": rule_data(name, actions, conditions, keep_going)}, timeout=5)
    return reply["id"]


async def apply(probe, profile_id, lines, **extra) -> dict:
    reply = await probe.request("rules.apply", {
        "profile_id": profile_id, "lines": [{"key": k, "text": t} for k, t in lines.items()], **extra}, timeout=5)
    return RulesApplyReply.model_validate(reply).model_dump()


# --- rules.apply: lines with keys in, answers by key out ----------------------------------------

@pytest.mark.asyncio
async def test_every_line_is_answered_under_its_own_key(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "серия и сезон", [extract("extract_single", "серия ", "N"),
                                                     extract("extract_season", "сезон ", "N")])
    reply = await apply(probe, profile, {"b": "серия 7", "a": "сезон 2 серия 5"})
    assert set(reply["results"]) == {"a", "b"}
    assert (reply["results"]["a"]["season"], reply["results"]["a"]["episode"]) == (2, 5)
    assert (reply["results"]["b"]["season"], reply["results"]["b"]["episode"]) == (1, 7)
    assert reply["invalid_rules"] == []


@pytest.mark.asyncio
async def test_an_excluded_line_has_no_numbers_even_with_a_card_season(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "трейлеры", [{"action_type": "exclude", "action_pattern": "[]"}],
                   [contains("трейлер")])
    await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    reply = await apply(probe, profile, {"t": "трейлер серия 3", "s": "серия 3"}, series_season=2)
    trailer, episode = reply["results"]["t"], reply["results"]["s"]
    assert trailer["excluded"] is True
    assert [trailer[k] for k in ("season", "episode", "start", "end", "part")] == [None] * 5
    assert trailer["events"] == [{"rule": "трейлеры", "action": "exclude"}]
    assert episode["excluded"] is False and episode["episode"] == 3


@pytest.mark.asyncio
async def test_the_season_of_the_card_is_the_fallback_for_a_line_without_season(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "серия и сезон", [extract("extract_single", "серия ", "N"),
                                                     extract("extract_season", "сезон ", "N")])
    for card in ("Season 3", 3, "3"):
        results = (await apply(probe, profile, {"own": "сезон 2 серия 1", "none": "серия 1"},
                               series_season=card))["results"]
        assert results["none"]["season"] == 3
        assert results["own"]["season"] == 2, "a season the rules extracted beats the card"
    assert (await apply(probe, profile, {"none": "серия 1"}))["results"]["none"]["season"] == 1, "the default season"


@pytest.mark.asyncio
async def test_a_single_without_a_part_is_part_one_where_the_player_and_the_voice_have_parts(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "серия и часть", [extract("extract_single", "серия ", "N"),
                                                      extract("extract_part", "часть ", "N")])
    lines = [{"key": "whole", "text": "серия 1", "player": "vk", "voice": "V"},
             {"key": "second", "text": "серия 1 часть 2", "player": "vk", "voice": "V"},
             {"key": "other", "text": "серия 1", "player": "kodik", "voice": "V"}]
    reply = await probe.request("rules.apply", {"profile_id": profile, "lines": lines}, timeout=5)
    results = RulesApplyReply.model_validate(reply).results
    assert [(key, results[key].part) for key in ("whole", "second", "other")] == [("whole", 1), ("second", 2), ("other", None)]


@pytest.mark.asyncio
async def test_a_repeated_key_is_refused_and_nothing_is_answered(system):
    probe = system.probe
    profile = await new_profile(probe)
    with pytest.raises(BusRequestError, match="повторяются.*'a'"):
        await probe.request("rules.apply", {"profile_id": profile, "lines": [
            {"key": "a", "text": "один"}, {"key": "b", "text": "два"}, {"key": "a", "text": "три"}]}, timeout=5)


@pytest.mark.asyncio
async def test_no_lines_is_an_empty_answer(system):
    probe = system.probe
    profile = await new_profile(probe)
    assert await apply(probe, profile, {}) == {"results": {}, "invalid_rules": []}


@pytest.mark.asyncio
async def test_the_request_is_checked_by_the_contract(system):
    probe = system.probe
    for payload in ({"lines": []},
                    {"profile_id": 1},
                    {"profile_id": 1, "lines": [{"text": "без ключа"}]},
                    {"profile_id": "не число", "lines": []}):
        with pytest.raises(BusRequestError, match="ValidationError"):
            await probe.request("rules.apply", payload, timeout=5)


@pytest.mark.asyncio
async def test_an_assigned_voiceover_is_the_voiceover_of_the_line_and_other_marks_ride_along(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "озвучка", [assign("assign_voiceover", "Студия"),
                                               assign("assign_quality", "WEB-DL")],
                   [contains("(озв)")], keep_going=True)
    await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    results = (await apply(probe, profile, {"v": "серия 4 (озв)", "p": "серия 5"}))["results"]
    assert (results["v"]["voiceover"], results["v"]["quality"], results["v"]["episode"]) == ("Студия", "WEB-DL", 4)
    assert results["p"]["voiceover"] is None and "quality" not in results["p"]


@pytest.mark.asyncio
async def test_a_range_is_answered_as_start_and_end_and_a_reversed_one_as_an_error(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "диапазон", [extract("extract_range", "N", "-", "N")])
    results = (await apply(probe, profile, {"up": "5-10 серии", "down": "10-5 серии"}))["results"]
    assert (results["up"]["start"], results["up"]["end"], results["up"]["errors"]) == (5, 10, [])
    down = results["down"]
    assert (down["start"], down["end"], down["episode"]) == (None, None, None)
    assert len(down["errors"]) == 1 and "обратный диапазон 10-5" in down["errors"][0]


@pytest.mark.asyncio
async def test_a_broken_rule_is_reported_with_every_answer_and_the_others_still_work(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    with sqlite3.connect(system.db_path) as conn:
        conn.execute("INSERT INTO parser_rules (profile_id, name, priority, action_pattern, continue_after_match) "
                     "VALUES (?, 'битое', 9, 'не json', 0)", (profile,))
    reply = await apply(probe, profile, {"s": "серия 2"})
    assert reply["results"]["s"]["episode"] == 2
    assert [bad["name"] for bad in reply["invalid_rules"]] == ["битое"]


@pytest.mark.asyncio
async def test_the_old_topics_are_gone(system):
    for topic in ("rules.test", "rules.cache.invalidate"):
        with pytest.raises(BusRequestError, match="нет обработчика"):
            await system.probe.request(topic, {}, timeout=5)


# --- the cache follows the rules --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_changed_rule_is_applied_by_the_next_answer(system):
    probe = system.probe
    profile = await new_profile(probe)
    rule = await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    assert (await apply(probe, profile, {"k": "серия 4"}))["results"]["k"]["episode"] == 4
    await probe.request("rules.rules.update", {"rule_id": rule, "data": rule_data(
        "серия", [extract("extract_single", "серия ", "N", ("add", "10"))])}, timeout=5)
    assert (await apply(probe, profile, {"k": "серия 4"}))["results"]["k"]["episode"] == 14
    await probe.request("rules.rules.delete", {"rule_id": rule}, timeout=5)
    assert (await apply(probe, profile, {"k": "серия 4"}))["results"]["k"]["episode"] is None


@pytest.mark.asyncio
async def test_a_profile_read_before_a_change_is_not_kept_after_it(system):
    probe, module = system.probe, system.rules
    profile = await new_profile(probe)
    rule = await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])

    read, may_finish = asyncio.Event(), asyncio.Event()
    real = module.repo.get_rules

    async def slow_read(profile_id):
        rules = await real(profile_id)
        read.set()
        await may_finish.wait()
        return rules
    module.repo.get_rules = slow_read
    stale = asyncio.create_task(apply(probe, profile, {"k": "серия 4"}))
    await asyncio.wait_for(read.wait(), 5)
    module.repo.get_rules = real
    await probe.request("rules.rules.update", {"rule_id": rule, "data": rule_data(
        "серия", [extract("extract_single", "серия ", "N", ("add", "10"))])}, timeout=5)
    may_finish.set()
    assert (await stale)["results"]["k"]["episode"] == 4, "the answer that started before the change is old"
    assert (await apply(probe, profile, {"k": "серия 4"}))["results"]["k"]["episode"] == 14


# --- rules.profile.changed --------------------------------------------------------------------

def changed(profile_id) -> tuple:
    return "rules.profile.changed", RulesProfileChanged(profile_id=profile_id).model_dump()


@pytest.mark.asyncio
async def test_every_kind_of_change_of_a_profile_is_announced_once(system):
    probe = system.probe
    sub = system.bus.subscribe("rules.profile.changed")

    profile = await new_profile(probe)
    assert drain(sub) == [], "a new profile is assigned to no source yet"

    await probe.request("rules.profiles.update", {"profile_id": profile, "data": {"name": "новое имя"}}, timeout=5)
    assert drain(sub) == [changed(profile)]

    rule = await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    assert drain(sub) == [changed(profile)]

    await probe.request("rules.rules.update", {"rule_id": rule, "data": rule_data(
        "серия 2", [extract("extract_single", "серия ", "N")])}, timeout=5)
    assert drain(sub) == [changed(profile)]

    await probe.request("rules.rules.delete", {"rule_id": rule}, timeout=5)
    assert drain(sub) == [changed(profile)]

    await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5)
    assert drain(sub) == [changed(profile)]


@pytest.mark.asyncio
async def test_a_change_that_changes_nothing_is_not_announced(system):
    probe = system.probe
    profile = await new_profile(probe)
    sub = system.bus.subscribe("rules.profile.changed")
    await probe.request("rules.rules.update", {"rule_id": 999, "data": rule_data("нет", [])}, timeout=5)
    await probe.request("rules.rules.delete", {"rule_id": 999}, timeout=5)
    await probe.request("rules.rules.reorder", {"ordered_ids": [999]}, timeout=5)
    await probe.request("rules.profiles.delete", {"profile_id": 999}, timeout=5)
    with pytest.raises(BusRequestError) as refused:
        await probe.request("rules.profiles.update", {"profile_id": 999, "data": {"name": "x"}}, timeout=5)
    assert code_of(refused.value)["code"] == "profile_not_found"
    assert drain(sub) == []
    assert rows(system.db_path, "SELECT id FROM parser_profiles") == [(profile,)]


@pytest.mark.asyncio
async def test_a_refused_change_is_not_announced(system):
    probe = system.probe
    profile = await new_profile(probe)
    sub = system.bus.subscribe("rules.profile.changed")
    with pytest.raises(BusRequestError, match="неизвестный тип действия"):
        await new_rule(probe, profile, "опечатка", [{"action_type": "assign_voiceoverr", "action_pattern": "X"}])
    assert drain(sub) == []


@pytest.mark.asyncio
async def test_reordering_announces_each_touched_profile_once(system):
    probe = system.probe
    first, second, untouched = [await new_profile(probe, name) for name in ("один", "два", "три")]
    a1 = await new_rule(probe, first, "a1", [extract("extract_single", "N")])
    a2 = await new_rule(probe, first, "a2", [extract("extract_single", "N")])
    b1 = await new_rule(probe, second, "b1", [extract("extract_single", "N")])
    await new_rule(probe, untouched, "c1", [extract("extract_single", "N")])
    sub = system.bus.subscribe("rules.profile.changed")

    await probe.request("rules.rules.reorder", {"ordered_ids": [a2, b1, a1]}, timeout=5)

    assert sorted(drain(sub), key=lambda item: item[1]["profile_id"]) == [changed(first), changed(second)]
    order = dict(rows(system.db_path, "SELECT id, priority FROM parser_rules"))
    assert (order[a2], order[b1], order[a1]) == (0, 1, 2)


@pytest.mark.asyncio
async def test_the_layout_that_hears_the_event_gets_the_new_rules_at_once(system_with_layout):
    probe, recounter = system_with_layout.probe, system_with_layout.recounter
    profile = await new_profile(probe)
    rule = await new_rule(probe, profile, "серия", [extract("extract_single", "серия ", "N")])
    await asyncio.wait_for(recounter.answered.wait(), 5)
    assert recounter.answers[-1].results["k"].episode == 4
    recounter.answered.clear()

    await probe.request("rules.rules.update", {"rule_id": rule, "data": rule_data(
        "серия", [extract("extract_single", "серия ", "N", ("add", "10"))])}, timeout=5)
    await asyncio.wait_for(recounter.answered.wait(), 5)
    assert recounter.answers[-1].results["k"].episode == 14, "the event came after the write and the cache reset"


# --- rules.format_filename: numbers are ready, no rule is applied ---------------------------------

def card(**fields) -> dict:
    return {"name_en": "Show", "kind": "video", "season": None, **fields}


def item(**fields) -> dict:
    return {"season": 1, "episode_start": 5, "episode_end": None, "part": None, "resolution": "1080",
            "voiceover_tag": None, "source_title": "Серия 5", **fields}


async def filename(probe, series, media_item, **extra) -> str:
    reply = await probe.request("rules.format_filename", {"series": series, "media_item": media_item, **extra},
                                timeout=5)
    return reply["filename"]


@pytest.mark.asyncio
async def test_the_name_takes_the_numbers_of_the_item_and_never_applies_rules_again(system):
    probe = system.probe
    # applying this profile to the title of the item would give episode 105 and season 7
    profile = await new_profile(probe)
    await new_rule(probe, profile, "сдвиг", [extract("extract_single", "серия ", "N", ("add", "100")),
                                             assign("assign_season", "7"), assign("assign_voiceover", "Чужая")])
    reads = []
    real = system.rules.repo.get_rules

    async def counting(profile_id):
        reads.append(profile_id)
        return await real(profile_id)
    system.rules.repo.get_rules = counting

    name = await filename(probe, card(parser_profile_id=profile), item(voiceover_tag="Студия"))
    assert name == "Show s01e05 [Студия] 1080p.mp4"
    assert reads == [], "the profile is not read for a file name"


@pytest.mark.asyncio
async def test_the_name_of_a_range_a_part_and_an_override(system):
    probe = system.probe
    spliced = item(episode_start=3, episode_end=5, part=2)
    assert await filename(probe, card(), spliced) == "Show s01e03-e05 Part 2 1080p.mp4"
    assert await filename(probe, card(), spliced, episode_override=4) == "Show s01e04 Part 2 1080p.mp4"
    assert await filename(probe, card(), item(), original_filename="Season 01/old.mp4") \
        == "Season 01/Show s01e05 1080p.mp4"


@pytest.mark.asyncio
async def test_a_single_episode_is_named_alike_whether_its_end_is_empty_or_equal_to_its_start(system):
    probe = system.probe
    for end in (None, 5):
        assert await filename(probe, card(), item(episode_end=end)) == "Show s01e05 1080p.mp4"
        assert await filename(probe, card(), item(episode_end=end, part=2)) == "Show s01e05 Part 2 1080p.mp4"


@pytest.mark.asyncio
async def test_the_extension_follows_the_kind_of_the_series(system):
    probe = system.probe
    assert (await filename(probe, card(kind="video"), item(), original_filename="x.mkv")).endswith(".mkv")  # packed
    assert (await filename(probe, card(kind="video"), item(), original_filename="x.mp4")).endswith(".mp4")
    assert (await filename(probe, card(kind="video"), item())).endswith(".mp4")
    assert (await filename(probe, card(kind="torrent"), item(), original_filename="x.AVI")).endswith(".avi")
    assert (await filename(probe, card(kind="torrent"), item())).endswith(".mkv")
    # the column that left the series decides nothing
    assert (await filename(probe, card(kind="torrent", source_type="vk_video"), item())).endswith(".mkv")


@pytest.mark.asyncio
async def test_the_card_fills_what_the_item_does_not_have(system):
    probe = system.probe
    series = card(season="Season 3", quality_override="BDRip", resolution_override="2160p")
    assert await filename(probe, series, item(season=None)) == "Show s03e05 BDRip 2160p.mp4"


# --- rules.format_torrent_file: the profile is a parameter ----------------------------------------

async def torrent_name(probe, series, basename, **extra) -> dict:
    return await probe.request("rules.format_torrent_file", {
        "series": series, "file_basename": basename, "original_path": f"Show/{basename}", **extra}, timeout=5)


@pytest.mark.asyncio
async def test_the_torrent_file_is_named_by_the_profile_of_the_request(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "сезон и серия", [extract("extract_season", "S", "N"),
                                                     extract("extract_single", "E", "N")])
    series = card(kind="torrent")
    reply = await torrent_name(probe, series, "Show.S02E05.mkv", profile_id=profile)
    assert reply["filename"] == "Season 02/Show s02e05.mkv"
    assert (reply["extracted"]["season"], reply["extracted"]["episode"]) == (2, 5)


@pytest.mark.asyncio
async def test_the_torrent_profile_is_required_and_not_taken_from_the_series(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "серия", [extract("extract_single", "E", "N")])
    for extra in ({}, {"profile_id": None}):
        with pytest.raises(BusRequestError, match="profile_id"):
            await torrent_name(probe, card(kind="torrent", parser_profile_id=profile), "Show.E05.mkv", **extra)


@pytest.mark.asyncio
async def test_what_the_rules_extracted_beats_the_card_for_a_torrent_file(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "метки", [assign("assign_quality", "WEB-DL"), assign("assign_resolution", "1080"),
                                             extract("extract_single", "E", "N")])
    series = card(kind="torrent", season="1", quality_override="BDRip", resolution_override="2160p")
    reply = await torrent_name(probe, series, "Show.E03.mkv", profile_id=profile)
    assert reply["filename"] == "Season 01/Show s01e03 WEB-DL 1080p.mkv"


@pytest.mark.asyncio
async def test_an_extracted_season_beats_the_season_of_the_card_for_a_torrent_file(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "сезон и серия", [extract("extract_season", "S", "N"),
                                                     extract("extract_single", "E", "N")])
    reply = await torrent_name(probe, card(kind="torrent", season="1"), "Show.S02E05.mkv", profile_id=profile)
    assert reply["filename"] == "Season 02/Show s02e05.mkv"
    unreadable = await torrent_name(probe, card(kind="torrent", season="Season"), "Show.E05.mkv", profile_id=profile)
    assert unreadable["filename"] is None


# --- a profile that is not there --------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_operation_on_a_missing_profile_is_refused_with_the_same_code(system):
    probe = system.probe
    ask = {
        "rules.apply": {"profile_id": 99, "lines": [{"key": "k", "text": "серия 1"}]},
        "rules.format_torrent_file": {"series": card(kind="torrent"), "profile_id": 99,
                                      "file_basename": "a.mkv", "original_path": "a.mkv"},
        "rules.rules.list": {"profile_id": 99},
        "rules.rules.add": {"profile_id": 99, "data": rule_data("r", [])},
        "rules.profiles.update": {"profile_id": 99, "data": {"name": "x"}},
    }
    sub = system.bus.subscribe("rules.profile.changed")
    for topic, payload in ask.items():
        with pytest.raises(BusRequestError) as refused:
            await probe.request(topic, payload, timeout=5)
        assert code_of(refused.value) == {"code": "profile_not_found", "params": {"profile_id": 99}}, topic
    assert drain(sub) == []
    assert rows(system.db_path, "SELECT id FROM parser_rules") == []
    assert 99 not in system.rules._cache


@pytest.mark.asyncio
async def test_a_profile_asked_for_before_it_exists_is_not_remembered_as_empty(system):
    probe = system.probe
    first = await new_profile(probe, "один")
    with pytest.raises(BusRequestError):
        await apply(probe, first + 1, {"k": "серия 4"})
    second = await new_profile(probe, "два")
    assert second == first + 1
    await new_rule(probe, second, "серия", [extract("extract_single", "серия ", "N")])
    assert (await apply(probe, second, {"k": "серия 4"}))["results"]["k"]["episode"] == 4


# --- deleting a profile --------------------------------------------------------------------

async def profile_with_rules(probe, name="профиль") -> tuple[int, list[int]]:
    profile = await new_profile(probe, name)
    ids = [await new_rule(probe, profile, rule, [extract("extract_single", "серия ", "N")], [contains("сер")])
           for rule in ("первое", "второе")]
    return profile, ids


def stored(db_path, profile) -> dict:
    return {
        "profiles": len(rows(db_path, "SELECT id FROM parser_profiles WHERE id=?", (profile,))),
        "rules": len(rows(db_path, "SELECT id FROM parser_rules WHERE profile_id=?", (profile,))),
        "conditions": len(rows(db_path, "SELECT c.id FROM parser_rule_conditions c JOIN parser_rules r "
                                        "ON r.id=c.rule_id WHERE r.profile_id=?", (profile,))),
    }


@pytest.mark.asyncio
async def test_a_profile_read_by_sources_is_not_deleted(system):
    probe = system.probe
    profile, _ = await profile_with_rules(probe)
    system.catalog.readers[profile] = [{"id": 1}, {"id": 2}]
    sub = system.bus.subscribe("rules.profile.changed")
    with pytest.raises(BusRequestError) as refused:
        await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5)
    assert code_of(refused.value) == {"code": "profile_in_use", "params": {"profile_id": profile, "sources": 2}}
    assert stored(system.db_path, profile) == {"profiles": 1, "rules": 2, "conditions": 2}
    assert drain(sub) == []


@pytest.mark.asyncio
async def test_a_profile_nobody_reads_is_deleted_with_its_rules_and_announced(system):
    probe = system.probe
    profile, _ = await profile_with_rules(probe)
    other, _ = await profile_with_rules(probe, "другой")
    sub = system.bus.subscribe("rules.profile.changed")
    assert await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5) == {"ok": True}
    assert system.catalog.asked == [profile]
    assert stored(system.db_path, profile) == {"profiles": 0, "rules": 0, "conditions": 0}
    assert stored(system.db_path, other) == {"profiles": 1, "rules": 2, "conditions": 2}
    assert drain(sub) == [changed(profile)]


def take_the_profile_by_a_source(db_path, profile):
    with sqlite3.connect(db_path) as conn:
        insert_series(conn, sources=[source(url=SITE, source_type="web_video", parser_profile_id=profile)])


@pytest.mark.asyncio
async def test_a_source_that_took_the_profile_during_the_delete_is_a_refusal_with_the_count(system):
    probe = system.probe
    profile, _ = await profile_with_rules(probe)
    # the catalog still says "nobody" when asked first, and names the source when asked again
    take_the_profile_by_a_source(system.db_path, profile)
    system.catalog.script = [[], [{"id": 1}]]
    sub = system.bus.subscribe("rules.profile.changed")
    with pytest.raises(BusRequestError) as refused:
        await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5)
    assert code_of(refused.value) == {"code": "profile_in_use", "params": {"profile_id": profile, "sources": 1}}
    assert system.catalog.asked == [profile, profile]
    assert stored(system.db_path, profile) == {"profiles": 1, "rules": 2, "conditions": 2}
    assert drain(sub) == []
    assert (await apply(probe, profile, {"k": "серия 4"}))["results"]["k"]["episode"] == 4


@pytest.mark.asyncio
async def test_a_database_refusal_the_catalog_does_not_explain_is_passed_on_as_it_is(system):
    probe = system.probe
    profile, _ = await profile_with_rules(probe)
    take_the_profile_by_a_source(system.db_path, profile)
    with pytest.raises(BusRequestError, match="FOREIGN KEY"):
        await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5)
    assert system.catalog.asked == [profile, profile]
    assert stored(system.db_path, profile) == {"profiles": 1, "rules": 2, "conditions": 2}


@pytest.mark.asyncio
async def test_a_profile_is_not_deleted_when_the_catalog_cannot_be_asked(system_without_catalog):
    probe = system_without_catalog.probe
    profile, _ = await profile_with_rules(probe)
    with pytest.raises(BusRequestError, match="нет обработчика"):
        await probe.request("rules.profiles.delete", {"profile_id": profile}, timeout=5)
    assert stored(system_without_catalog.db_path, profile) == {"profiles": 1, "rules": 2, "conditions": 2}


@pytest.mark.asyncio
async def test_a_rule_with_a_broken_condition_is_not_left_half_written(system):
    profile = await new_profile(system.probe)
    with pytest.raises(sqlite3.IntegrityError):
        await system.rules.repo.add_rule(profile, {"name": "r", "conditions": [
            {"condition_type": "contains", "pattern": "[]"}, {"condition_type": None, "pattern": "[]"}]})
    assert stored(system.db_path, profile) == {"profiles": 1, "rules": 0, "conditions": 0}


# --- a profile is an id and a name ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_profile_is_an_id_and_a_name(system):
    probe = system.probe
    profile = await new_profile(probe, "один")
    assert await probe.request("rules.profiles.list", {}, timeout=5) == [{"id": profile, "name": "один"}]
    assert "preferred_voiceovers" not in [row[1] for row in rows(system.db_path, "PRAGMA table_info(parser_profiles)")]


def test_the_unused_profile_column_goes_and_comes_back_without_touching_rules(tmp_path):
    path = tmp_path / "migration.db"
    alembic(path, "upgrade", "0029")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name, preferred_voiceovers) VALUES (1, 'один', 'Студия')")
        conn.execute("INSERT INTO parser_rules (id, profile_id, name, priority, action_pattern, continue_after_match) "
                     "VALUES (1, 1, 'r', 1, '[]', 0)")
        conn.execute("INSERT INTO parser_rule_conditions (id, rule_id, condition_type, pattern, logical_operator) "
                     "VALUES (1, 1, 'contains', '[]', 'AND')")

    def kept():
        return rows(path, "SELECT p.id, p.name, r.id, r.name, r.priority, c.id, c.pattern FROM parser_profiles p "
                          "JOIN parser_rules r ON r.profile_id=p.id JOIN parser_rule_conditions c ON c.rule_id=r.id")

    def columns():
        return [row[1] for row in rows(path, "PRAGMA table_info(parser_profiles)")]

    before = kept()
    assert before and "preferred_voiceovers" in columns()
    alembic(path, "upgrade", "0030")
    assert "preferred_voiceovers" not in columns() and kept() == before
    alembic(path, "downgrade", "0029")
    assert "preferred_voiceovers" in columns() and kept() == before


@pytest.mark.asyncio
async def test_an_assigned_range_is_answered_as_start_and_end_for_the_lines_the_rule_names(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "склейка 109-110", [assign("assign_range", "109-110")], [contains("110 серия")])
    await new_rule(probe, profile, "серия", [extract("extract_single", "N", " серия")])
    results = (await apply(probe, profile, {"c": "AniMy | 110 серия", "s": "AniMy | 111 серия"}))["results"]
    assert (results["c"]["start"], results["c"]["end"]) == (109, 110)
    assert (results["s"]["start"], results["s"]["end"], results["s"]["episode"]) == (None, None, 111)


@pytest.mark.asyncio
async def test_a_rule_with_a_wrong_assigned_range_is_not_saved(system):
    probe = system.probe
    profile = await new_profile(probe)
    with pytest.raises(BusRequestError, match="диапазон"):
        await new_rule(probe, profile, "склейка", [assign("assign_range", "110-109")])
