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
from core.contracts import variant_target

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


async def test_a_release_added_as_a_source_is_downloaded_read_and_synced_into_the_library(stand):
    await stand.configure([VOICE])
    saved = await stand.call("POST", "/api/settings/torrent_staging", {"value": stand.staging})
    assert saved["value"] == stand.staging
    page = stand.torrent_page("show", "Сериал", {"Show - 01.mkv": 1, "Show - 02.mkv": 2})
    made = await stand.call("POST", "/api/series", {"name": "Сериал", "name_en": "Show", "site": "anilibria",
                                                    "save_path": stand.library, "sources": [
                                                        {"url": page.url, "source_type": "torrent",
                                                         "parser_profile_id": await torrent_profile(stand)}]})
    series_id = made["series_id"]

    listed = await stand.call("POST", "/api/torrent/page", {"url": page.url})
    assert [each["episodes"] for each in listed["releases"]] == ["1-2"]
    release = listed["releases"][0]["release"]
    await stand.call("POST", "/api/torrent/download", {"url": page.url, "release": release})
    # No rule is the source's yet: the owner writes one in the tab «Файлы», and every episode is placed by it.
    profiles = (await stand.call("POST", "/api/torrent/editor", {"url": page.url}))["profiles"]
    action = [{"action_type": "extract_single", "action_pattern": json.dumps([{"type": "text", "value": " - "}, {"type": "number"}])}]
    await stand.call("POST", f"/api/parser-profiles/{profiles['files']}/rules", {
        "name": "серия", "action_pattern": json.dumps(action), "continue_after_match": False, "conditions": []})
    await sound_rule(stand, profiles["voice_streams"])
    await until_table(stand, page.url, 2)
    kept = (await stand.call("POST", "/api/torrent/releases", {"url": page.url}))["releases"]
    assert [(each["state"], os.path.dirname(each["folder"])) for each in kept] == [("ready", stand.staging)]

    view = await stand.sync(series_id)
    assert sorted(each["episode"] for each in view["assets"]) == [[1, 1], [1, 2]]
    await stand.check(series_id)


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

    queue = await until_subtitles(stand, series_id, "Надписи")
    assert [(row["group"], row["language"], row["subtitle_type"], row["default"], row["forced"]) for row in queue] == [
        ("Надписи", "rus", "signs", False, False)]
    target = variant_target(queue[0]["group"], queue[0]["source_id"], queue[0]["player"])
    await stand.call("PUT", f"/api/series/{series_id}/output/settings/batch", {"items": [
        {"lever": "subtitles.default", "target": target, "value": True},
        {"lever": "subtitles.forced", "target": target, "value": True}]})
    queue = (await stand.call("GET", f"/api/series/{series_id}/output/queues"))["subtitles"]["rows"]
    assert [(row["default"], row["default_level"], row["forced"], row["forced_level"]) for row in queue] == [
        (True, "series", True, "series")]

    view = await stand.sync(series_id)
    assert [each["episode"] for each in view["assets"]] == [[1, 1]]
    held = await container.contents(os.path.join(stand.library, view["assets"][0]["name"]))
    assert [(each.title, each.language, each.default, each.forced) for each in held.subtitles] == [("Надписи", "rus", True, True)]


async def test_two_versions_of_an_episode_tagged_are_two_variants_of_the_output_either_one_a_reference(stand):
    """«Директор» (first) gives the director's cut its number and the tag DIR, «Серия» the plain one: `S:1 | E:1 | TG:DIR` and `S:1 | E:1`
    are two episodes of the editor and two variants of the reference of the episode on the output."""
    await stand.configure([VOICE])
    await stand.call("POST", "/api/settings/torrent_staging", {"value": stand.staging})
    page = stand.torrent_page("cut", "Сериал", {"Show 01.mkv": 1, "Show 01Dir.mkv": 1})
    made = await stand.call("POST", "/api/series", {"name": "Сериал", "name_en": "Show", "site": "anilibria",
                                                    "save_path": stand.library, "sources": [
                                                        {"url": page.url, "source_type": "torrent",
                                                         "parser_profile_id": await torrent_profile(stand)}]})
    release = (await stand.call("POST", "/api/torrent/page", {"url": page.url}))["releases"][0]["release"]
    await stand.call("POST", "/api/torrent/download", {"url": page.url, "release": release})
    profiles = (await stand.call("POST", "/api/torrent/editor", {"url": page.url}))["profiles"]
    # A rule acts on the lines its conditions take: the tag is given to the files that say «Dir».
    for name, blocks, tag in (("Директор", [{"type": "text", "value": " "}, {"type": "number"}, {"type": "text", "value": "Dir"}], "DIR"),
                              ("Серия", [{"type": "text", "value": " "}, {"type": "number"}], "")):
        actions = [{"action_type": "extract_single", "action_pattern": json.dumps(blocks, ensure_ascii=False)}]
        actions += [{"action_type": "assign_video_tag", "action_pattern": tag}] if tag else []
        conditions = [{"condition_type": "contains", "logical_operator": "AND",
                       "pattern": json.dumps([{"type": "text", "value": "Dir"}])}] if tag else []
        await stand.call("POST", f"/api/parser-profiles/{profiles['files']}/rules", {
            "name": name, "action_pattern": json.dumps(actions, ensure_ascii=False), "continue_after_match": False,
            "conditions": conditions})
    await sound_rule(stand, profiles["voice_streams"])
    await until_table(stand, page.url, 2)
    files = (await stand.call("POST", "/api/torrent/editor", {"url": page.url}))["view"]["tabs"]["files"]
    assert sorted((each["path"].split("/")[-1], each["place"]["tag"], each["conflict"]) for each in files) == [
        ("Show 01.mkv", "", None), ("Show 01Dir.mkv", "DIR", None)]
    # The layout of the series is counted anew by the events of the read (composition.changed): the reference of the episode
    # has both versions as two variants.
    changed = stand.bus.subscribe("composition.changed")
    while True:
        queue = await stand.call("GET", f"/api/series/{made['series_id']}/output/queues?season=1&episode=1")
        if len(queue["reference"]["rows"]) >= 2:
            break
        await asyncio.wait_for(changed.queue.get(), STAND_LIMIT_S)
    assert sorted((row["player"], row["player_label"]) for row in queue["reference"]["rows"]) == [
        (f"torrent:{release}", f"Сериал · 1-2 {release}"), (f"torrent:{release}#DIR", f"Сериал · 1-2 {release} · DIR")]
