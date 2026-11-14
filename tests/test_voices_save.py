"""The save of the voices settings on the stand of the whole application (tests/app_stand.py): a group of voice-overs is saved
whatever the order of sources holds, and the order stays as the user left it."""
import json

import pytest

from app_stand import AppStand


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path))
    await made.start()
    yield made
    await made.stop()
    made.close()


async def stored(stand: AppStand) -> dict:
    return (await stand.module("voices").request("voices.settings.export", {}))["values"]


async def test_a_group_is_saved_while_the_order_holds_a_line_the_sources_do_not_know(stand):
    voices = stand.module("voices")
    # The order the user saved while the provider was up: its line is among the players, and now no provider names it.
    await voices.request("settings.values.set_many", {"values": {"voices.source_priority": json.dumps(["line:CO4K", "kodik", "cvh"])}})
    shown = await stand.call("GET", "/api/voices/settings")
    assert "line:CO4K" in shown["priority"]

    # What the screen sends: every field of the sheet, the order as it was shown, and the group that is new.
    saved = await stand.call("PUT", "/api/voices/settings", {
        "priority": shown["priority"], "players_off": shown["players_off"],
        "voice_groups": {**shown["voice_groups"], "New Horizons Studio": []},
        "voice_languages": {**shown["voice_languages"], "New Horizons Studio": "rus"}})
    assert saved["saved"], saved["problems"]
    assert "New Horizons Studio" in saved["settings"]["voice_groups"]

    values = await stored(stand)
    assert json.loads(values["voices.groups"]) == {"New Horizons Studio": []}
    assert json.loads(values["voices.source_priority"]) == ["line:CO4K", "kodik", "cvh"]  # the order is not rewritten


async def test_a_new_player_the_sources_do_not_know_is_still_refused(stand):
    shown = await stand.call("GET", "/api/voices/settings")
    refused = await stand.call("PUT", "/api/voices/settings", {
        "priority": [*shown["priority"], "line:nowhere"], "players_off": shown["players_off"],
        "voice_groups": shown["voice_groups"], "voice_languages": shown["voice_languages"]})
    assert not refused["saved"]
    assert [(each["field"], each["detail"]["code"]) for each in refused["problems"]] == [("priority", "priority_unknown_player")]
