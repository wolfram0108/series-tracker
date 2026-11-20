"""The constructor over the bus (stage 10, П-2): rules.apply by key, rules.profile.changed, file names from
ready-made numbers, the profile of a torrent file from the request, and the deletion of a profile.
"""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.contracts import RulesApplyReply, RulesProfileChanged
from core.db import Database
from core.reasons import parse
from modules.rules import RulesModule
from series_factory import insert_series, source
from schema_db import migrated

SITE = "https://dunhuatv.ru/title/x"


@pytest.fixture
def db_path(tmp_path):
    return migrated(tmp_path / "test.db")


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


# --- the cache follows the rules --------------------------------------------------------------

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


# --- rules.format_filename: numbers are ready, no rule is applied ---------------------------------

def card(**fields) -> dict:
    return {"name_en": "Show", "season": None, **fields}


def item(**fields) -> dict:
    return {"season": 1, "episode_start": 5, "episode_end": None, "part": None, "resolution": "1080",
            "voiceover_tag": None, "source_title": "Серия 5", **fields}


async def filename(probe, series, media_item, **extra) -> str:
    reply = await probe.request("rules.format_filename", {"series": series, "media_item": media_item, **extra},
                                timeout=5)
    return reply["filename"]


@pytest.mark.asyncio
async def test_the_name_of_a_range_a_part_and_an_override(system):
    probe = system.probe
    spliced = item(episode_start=3, episode_end=5, part=2)
    assert await filename(probe, card(), spliced) == "Show s01e03-e05 Part 2 1080p.mkv"
    assert await filename(probe, card(), spliced, episode_override=4) == "Show s01e04 Part 2 1080p.mkv"
    assert await filename(probe, card(), item(), original_filename="Season 01/old.mp4") \
        == "Season 01/Show s01e05 1080p.mp4"


# --- rules.format_torrent_file: the profile is a parameter ----------------------------------------

async def torrent_name(probe, series, basename, **extra) -> dict:
    return await probe.request("rules.format_torrent_file", {
        "series": series, "file_basename": basename, "original_path": f"Show/{basename}", **extra}, timeout=5)


@pytest.mark.asyncio
async def test_what_the_rules_extracted_beats_the_card_for_a_torrent_file(system):
    probe = system.probe
    profile = await new_profile(probe)
    await new_rule(probe, profile, "метки", [assign("assign_quality", "WEB-DL"), assign("assign_resolution", "1080"),
                                             extract("extract_single", "E", "N")])
    series = card(kind="torrent", season="1", quality_override="BDRip", resolution_override="2160p")
    reply = await torrent_name(probe, series, "Show.E03.mkv", profile_id=profile)
    assert reply["filename"] == "Season 01/Show s01e03 WEB-DL 1080p.mkv"


# --- a profile that is not there --------------------------------------------------------------

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


# --- a profile is an id and a name ----------------------------------------------------------------

# --- the rule «Таблица» -------------------------------------------------------------------------------
