"""Tests of the voices module: voice identity, reference choice, layout of a series."""
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.settings import SettingsModule
from modules.voices import VoicesModule
from modules.voices.identity import same_voice, tokens, voice_groups
from modules.voices.layout import (DEFAULT_PRIORITY, choose_reference, download_chain,
                                   plan_layout, rank)


def src(player, studio, episodes, kind="", url=None):
    return {"url": url or f"https://site/?player={player}&voice={studio}", "player": player,
            "studio": studio, "kind": kind, "episodes": list(episodes)}


# The real "Стать победителем" catalog: three voices in CVH, the same three in Kodik.
WINNER = [
    src("cvh", "BlackCat", [*range(1, 11), 13], "Дубляж"),
    src("cvh", "RedHeadSound", range(1, 5), "Многоголосый"),
    src("cvh", "FumoDub", range(1, 16), "Одноголосый"),
    src("kodik", "FumoDub", range(1, 16), "voice"),
    src("kodik", "Red Head Sound", range(1, 16), "voice"),
    src("kodik", "BlackCat", range(1, 16), "voice"),
]
FUMODUB_CVH = WINNER[2]["url"]


# --- identity -------------------------------------------------------------------------


def test_names_differing_in_case_and_spaces_are_one_voice():
    assert same_voice("RedHeadSound", "Red Head Sound")
    assert same_voice("BLACKCAT", "blackcat")
    assert not same_voice("BlackCat", "FumoDub")


def test_a_combined_credit_names_every_studio_in_it():
    assert tokens("AniMy / RuChiMe") == {"animy", "ruchime"}
    assert same_voice("AniMy / RuChiMe", "Animy")
    assert same_voice("AniMy / RuChiMe", "RuChiMe")


def test_groups_are_transitive_and_keep_listing_order():
    names = ["AniMy / RuChiMe", "AniStar", "Animy", "RuChiMe", "AniStar & Other"]
    assert voice_groups(names) == [[0, 2, 3], [1, 4]]


def test_an_alias_joins_two_spellings(monkeypatch):
    monkeypatch.setattr("modules.voices.identity.ALIASES", {"aniliberty": "anilibria"})
    assert same_voice("AniLiberty", "AniLibria")


# --- reference choice -----------------------------------------------------------------


def test_reference_is_the_biggest_resolution():
    chosen = choose_reference([
        {"player": "alloha", "resolution": 720}, {"player": "kodik", "resolution": 1080}])
    assert chosen["player"] == "kodik"


def test_equal_resolution_goes_to_the_higher_priority_source():
    candidates = [{"player": "kodik", "resolution": 720}, {"player": "cvh", "resolution": 720},
                  {"player": "allvideo", "resolution": 720}]
    assert choose_reference(candidates)["player"] == "cvh"
    assert [rank(p) for p in ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")] == list(range(6))
    assert rank("unknown") == len(DEFAULT_PRIORITY)


def test_full_tie_keeps_the_first_listed_and_a_custom_order_wins():
    first, second = {"player": "cvh", "resolution": 720, "id": 1}, {"player": "cvh", "resolution": 720, "id": 2}
    assert choose_reference([first, second])["id"] == 1
    candidates = [{"player": "cvh", "resolution": 720}, {"player": "kodik", "resolution": 720}]
    assert choose_reference(candidates, ["kodik", "cvh"])["player"] == "kodik"
    with pytest.raises(ValueError):
        choose_reference([])


# --- layout of a series ---------------------------------------------------------------


def test_winner_layout_matches_the_agreed_example():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    rows = {row["label"]: row for row in layout["voices"]}
    assert rows["FumoDub"]["role"] == "reference"
    assert rows["FumoDub"]["source"]["player"] == "cvh"
    # The other two come from Kodik, which has every episode; CVH is their fallback.
    for label in ("RedHeadSound", "BlackCat"):
        assert rows[label]["role"] == "dub"
        assert rows[label]["source"]["player"] == "kodik"
        assert [f["player"] for f in rows[label]["fallbacks"]] == ["cvh"]
    # The reference comes first, the other voices keep the order of the site.
    assert [row["label"] for row in layout["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]
    assert layout["reference"]["url"] == FUMODUB_CVH
    # The reference voice is already in the video: its Kodik copy is not downloaded.
    assert [(s["player"], s["studio"]) for s in layout["skipped"]] == [("kodik", "FumoDub")]
    assert layout["skipped"][0]["reason"] == "озвучка уже в референсе"


def test_priority_breaks_a_tie_between_equally_full_sources():
    layout = plan_layout([src("kodik", "A", [1, 2]), src("cvh", "A", [1, 2]),
                          src("cvh", "Ref", [1, 2])], "manual",
                         "https://site/?player=cvh&voice=Ref")
    row = next(r for r in layout["voices"] if r["label"] == "A")
    assert row["source"]["player"] == "cvh"
    assert [f["player"] for f in row["fallbacks"]] == ["kodik"]


def test_fallbacks_follow_priority_not_coverage():
    sources = [src("kodik", "A", [1, 2, 3]), src("vibix", "A", [1]), src("cvh", "A", [1, 2]),
               src("cvh", "Ref", [1])]
    layout = plan_layout(sources, "manual", "https://site/?player=cvh&voice=Ref")
    row = next(r for r in layout["voices"] if r["label"] == "A")
    assert row["source"]["player"] == "kodik"
    assert [f["player"] for f in row["fallbacks"]] == ["cvh", "vibix"]


def test_download_chain_starts_with_the_main_source_and_skips_holes():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    red_head = next(r for r in layout["voices"] if r["label"] == "RedHeadSound")
    assert [s["player"] for s in download_chain(red_head, 3)] == ["kodik", "cvh"]
    assert [s["player"] for s in download_chain(red_head, 9)] == ["kodik"]
    black_cat = next(r for r in layout["voices"] if r["label"] == "BlackCat")
    assert download_chain(black_cat, 12) == [black_cat["source"]]
    assert download_chain(black_cat, 99) == []


def test_a_combined_credit_is_one_voice_with_the_reference_of_its_studio():
    sources = [src("cvh", "AniMy / RuChiMe", [1, 2, 3]), src("kodik", "Animy", [1, 2, 3, 4]),
               src("kodik", "Other", [1, 2])]
    layout = plan_layout(sources, "manual", sources[0]["url"])
    assert [(r["label"], r["role"]) for r in layout["voices"]] == [
        ("AniMy / RuChiMe", "reference"), ("Other", "dub")]
    assert [s["studio"] for s in layout["skipped"]] == ["Animy"]


def test_auto_mode_lists_every_voice_and_excludes_nothing():
    layout = plan_layout(WINNER, "auto")
    assert layout["reference"] is None and layout["skipped"] == []
    assert {row["role"] for row in layout["voices"]} == {"dub"}
    assert len(layout["voices"]) == 3
    # Voices are ordered as they would win a resolution tie: FumoDub has a CVH source, the rest only Kodik.
    assert [row["label"] for row in layout["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]


def test_manual_mode_needs_a_known_reference():
    with pytest.raises(ValueError):
        plan_layout(WINNER, "manual", "https://nowhere")
    with pytest.raises(ValueError):
        plan_layout(WINNER, "manual", None)


def test_ranges_show_holes_in_each_source():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    black_cat_cvh = next(r for r in layout["voices"] if r["label"] == "BlackCat")["fallbacks"][0]
    assert (black_cat_cvh["ranges"], black_cat_cvh["count"]) == ("1-10, 13", 11)


# --- the module on the bus ------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    return str(path)


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    probe = Probe(bus)
    runner = Runner(bus, [SettingsModule(bus, db), VoicesModule(bus), probe])
    await runner.start()
    yield probe
    await runner.stop()


@pytest.mark.asyncio
async def test_layout_query_uses_the_default_priority(system):
    reply = await system.request("voices.layout", {"sources": WINNER, "reference": FUMODUB_CVH},
                                 timeout=5)
    assert reply["priority"] == list(DEFAULT_PRIORITY)
    assert [r["label"] for r in reply["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]


@pytest.mark.asyncio
async def test_layout_query_takes_the_priority_from_settings(system):
    await system.request("settings.value.set",
                         {"key": "voices.source_priority", "value": '["kodik", "cvh"]'}, timeout=5)
    sources = [src("cvh", "A", [1, 2]), src("kodik", "A", [1, 2]), src("cvh", "Ref", [1])]
    reply = await system.request("voices.layout", {
        "sources": sources, "reference": "https://site/?player=cvh&voice=Ref"}, timeout=5)
    assert reply["priority"] == ["kodik", "cvh"]
    assert next(r for r in reply["voices"] if r["label"] == "A")["source"]["player"] == "kodik"


@pytest.mark.asyncio
async def test_a_broken_priority_setting_falls_back_to_the_default(system):
    await system.request("settings.value.set",
                         {"key": "voices.source_priority", "value": "not json"}, timeout=5)
    reply = await system.request("voices.layout", {"sources": WINNER, "reference": FUMODUB_CVH},
                                 timeout=5)
    assert reply["priority"] == list(DEFAULT_PRIORITY)
