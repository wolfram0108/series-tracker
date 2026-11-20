"""A torrent source on the stand of the whole application (tests/app_stand.py): a page of anilibria whose release the model of
qBittorrent downloads, every module real. The user's path through the API: the folder of the releases, a profile of rules for
the lines of the release, the series with the page as its source, «Скачать раздачу», the read of its files, the sync. What is
checked is on disk: every file of the library is an episode of the release with its own sound."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from core import container

from app_stand import STAND_LIMIT_S, AppStand

pytestmark = pytest.mark.asyncio

VOICE = "AniLibria"
EPISODE = [{"type": "text", "value": "E:"}, {"type": "number"}]


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path), qbit=True)
    await made.start()
    yield made
    await made.stop()
    made.close()


async def torrent_profile(stand: AppStand, subtitles: bool = False) -> int:
    """The rule a user makes for the lines of a release (`S:1 | E:5 | <file>`): the number after «E:», the voice-over named;
    with `subtitles`, a rule before it gives the lines of subtitles (`VT:subtitles`) their number alone, keeping their name."""
    made = await stand.call("POST", "/api/parser-profiles", {"name": "серии раздачи"})
    blocks = json.dumps(EPISODE, ensure_ascii=False)
    if subtitles:
        own = [{"action_type": "extract_single", "action_pattern": blocks}]
        await stand.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
            "name": "субтитры", "action_pattern": json.dumps(own, ensure_ascii=False), "continue_after_match": False,
            "conditions": [{"condition_type": "contains", "pattern": json.dumps([{"type": "text", "value": "VT:subtitles"}],
                                                                                ensure_ascii=False), "logical_operator": "AND"}]})
    actions = [{"action_type": "extract_single", "action_pattern": blocks}, {"action_type": "assign_voiceover", "action_pattern": VOICE}]
    await stand.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
        "name": "серия", "action_pattern": json.dumps(actions, ensure_ascii=False), "continue_after_match": False,
        "conditions": [{"condition_type": "contains", "pattern": blocks, "logical_operator": "AND"}]})
    return made["id"]


async def sound_rule(stand: AppStand, profile_id: int) -> None:
    """The rule of the tab «Озв. конт.» a user writes for a sound the file gives no title and no language: it goes into the
    folder of the voice-over with its language — green, a row. Without it the sound is red and goes no further."""
    actions = [{"action_type": "assign_voice_folder", "action_pattern": VOICE}, {"action_type": "assign_language", "action_pattern": "rus"}]
    await stand.call("POST", f"/api/parser-profiles/{profile_id}/rules", {
        "name": "звук", "action_pattern": json.dumps(actions, ensure_ascii=False), "continue_after_match": False,
        "conditions": [{"condition_type": "contains", "pattern": json.dumps([{"type": "text", "value": "T:звук"}], ensure_ascii=False),
                        "logical_operator": "AND"}]})


async def until_table(stand: AppStand, url: str, rows: int) -> None:
    """The table of the page has its rows: the read the end of the read of the release starts (sources.table.ready)."""
    key = (await stand.module("sources").request("sources.site.resolve", {"url": url}))["canonical_url"]
    ready = stand.bus.subscribe("sources.table.ready")
    while True:
        got = await stand.module("sources").request("sources.table.get", {"url": key})
        if got.get("state") == "ready" and len((got.get("table") or {}).get("rows") or []) >= rows:
            return
        await asyncio.wait_for(ready.queue.get(), STAND_LIMIT_S)


async def until_subtitles(stand: AppStand, series_id: int, name: str) -> list[dict]:
    """The rows of the table of the subtitles of the series once its layout has the variant `name`: the layout is counted
    anew by the events of the read of the release (composition.changed)."""
    changed = stand.bus.subscribe("composition.changed")
    while True:
        rows = (await stand.call("GET", f"/api/series/{series_id}/output/queues"))["subtitles"]["rows"]
        if any(row["group"] == name for row in rows):
            return rows
        await asyncio.wait_for(changed.queue.get(), STAND_LIMIT_S)


ASS = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
       "[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:00.20,0:00:00.80,Default,Надпись\n")


async def test_the_output_lays_the_subtitles_of_a_release_in_the_container_default_and_forced_as_its_levers_say(stand):
    """The source tells of subtitles their name, language and type; how they lie in the container is the output's word
    (DESIGN.md 7.1.1): the boxes of the variant in the table of the subtitles, then the sync — and the file has them so."""
    await stand.configure([VOICE])
    await stand.call("POST", "/api/settings/torrent_staging", {"value": stand.staging})
    page = stand.torrent_page("subs", "Сериал", {"Show - 01.mkv": 1, "Show - 01.rus.ass": ASS})
    made = await stand.call("POST", "/api/series", {"name": "Сериал", "name_en": "Show", "site": "anilibria",
                                                    "save_path": stand.library, "sources": [
                                                        {"url": page.url, "source_type": "torrent",
                                                         "parser_profile_id": await torrent_profile(stand, subtitles=True)}]})
    series_id = made["series_id"]
    release = (await stand.call("POST", "/api/torrent/page", {"url": page.url}))["releases"][0]["release"]
    await stand.call("POST", "/api/torrent/download", {"url": page.url, "release": release})
    profiles = (await stand.call("POST", "/api/torrent/editor", {"url": page.url}))["profiles"]
    action = [{"action_type": "extract_single", "action_pattern": json.dumps([{"type": "text", "value": " - "}, {"type": "number"}])}]
    await stand.call("POST", f"/api/parser-profiles/{profiles['files']}/rules", {
        "name": "серия", "action_pattern": json.dumps(action), "continue_after_match": False, "conditions": []})
    await sound_rule(stand, profiles["voice_streams"])
    await until_table(stand, page.url, 1)
    # The file of subtitles has no name in it: the operator makes the folder, its language and its type, and puts it there.
    await stand.call("POST", "/api/torrent/editor/folder", {"url": page.url, "tab": "subtitles", "name": "Надписи",
                                                            "language": "rus", "subtitle_type": "signs"})
    items = [each["item"] for each in (await stand.call("POST", "/api/torrent/editor", {"url": page.url}))["view"]["tabs"]["subtitles"]]
    assert len(items) == 1
    await stand.call("POST", "/api/torrent/editor/assign", {"url": page.url, "tab": "subtitles", "items": items, "folder": "Надписи"})

    # The first track of signs is the forced one with nothing set, in the table of the series and in that of the season.
    queue = await until_subtitles(stand, series_id, "Надписи")
    assert [(row["group"], row["language"], row["subtitle_type"], row["default"], row["forced"]) for row in queue] == [
        ("Надписи", "rus", "signs", False, True)]
    queue = (await stand.call("GET", f"/api/series/{series_id}/output/queues?season=1"))["subtitles"]["rows"]
    assert [(row["default"], row["forced"]) for row in queue] == [(False, True)]

    view = await stand.sync(series_id)
    assert [each["episode"] for each in view["assets"]] == [[1, 1]]
    held = await container.contents(os.path.join(stand.library, view["assets"][0]["name"]))
    assert [(each.title, each.language, each.default, each.forced) for each in held.subtitles] == [("Надписи", "rus", False, True)]
