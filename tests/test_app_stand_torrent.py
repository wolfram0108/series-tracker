"""A torrent source on the stand of the whole application (tests/app_stand.py): a page of anilibria whose release the model of
qBittorrent downloads, every module real. The user's path through the API: the folder of the releases, a profile of rules for
the lines of the release, the series with the page as its source, «Скачать раздачу», the read of its files, the sync. What is
checked is on disk: every file of the library is an episode of the release with its own sound."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from app_stand import STAND_LIMIT_S, AppStand

pytestmark = pytest.mark.asyncio

VOICE = "AniLibria"
EPISODE = [{"type": "text", "value": "E"}, {"type": "number"}]


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path), qbit=True)
    await made.start()
    yield made
    await made.stop()
    made.close()


async def torrent_profile(stand: AppStand) -> int:
    """The rule a user makes for the lines of a release (`S01E05 | <file>`): the number after «E», the voice-over named."""
    made = await stand.call("POST", "/api/parser-profiles", {"name": "серии раздачи"})
    blocks = json.dumps(EPISODE, ensure_ascii=False)
    actions = [{"action_type": "extract_single", "action_pattern": blocks}, {"action_type": "assign_voiceover", "action_pattern": VOICE}]
    await stand.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
        "name": "серия", "action_pattern": json.dumps(actions, ensure_ascii=False), "continue_after_match": False,
        "conditions": [{"condition_type": "contains", "pattern": blocks, "logical_operator": "AND"}]})
    return made["id"]


async def until_table(stand: AppStand, url: str, rows: int) -> None:
    """The table of the page has its rows: the read the end of the read of the release starts (sources.table.ready)."""
    key = (await stand.module("sources").request("sources.site.resolve", {"url": url}))["canonical_url"]
    ready = stand.bus.subscribe("sources.table.ready")
    while True:
        got = await stand.module("sources").request("sources.table.get", {"url": key})
        if got.get("state") == "ready" and len((got.get("table") or {}).get("rows") or []) >= rows:
            return
        await asyncio.wait_for(ready.queue.get(), STAND_LIMIT_S)


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
    await until_table(stand, page.url, 2)
    kept = (await stand.call("POST", "/api/torrent/releases", {"url": page.url}))["releases"]
    assert [(each["state"], os.path.dirname(each["folder"])) for each in kept] == [("ready", stand.staging)]

    view = await stand.sync(series_id)
    assert sorted(each["episode"] for each in view["assets"]) == [[1, 1], [1, 2]]
    await stand.check(series_id)
