"""The gateway points of the output section: the composition of a series, its references and the levers.
Each answer is one owner's answer as it is; the gateway merges nothing and knows nothing of sites or players."""
import pytest

from core.contracts import CompositionReply, OutputSwitch, Reference, SettingsReset, SettingsSet
from core.reasons import parse, reason
from gateway_fakes import published, serving
from modules.gateway.schemas import OutputSettingsReset, OutputSettingsSet, OutputSwitchBody

VIDEO = {"id": 1, "kind": "video", "sources": []}
TORRENT = {"id": 2, "kind": "torrent", "sources": []}
LAYOUT = {"series_id": 1, "rev": 3, "levels": [], "settings": [], "sources": [
    {"source_id": 4, "url": "https://x.test/p", "status": "ready", "reason": None, "read_at": None, "players": []}],
    "rows": [{"series_id": 1, "source_id": 4, "row_key": "r1", "excluded": False, "exclusion": None, "season": 1,
              "episode_start": 1, "episode_end": 1, "part": None, "voice_group": "Group", "voice_label": "Group",
              "voice_name": "g", "voice_unknown": None, "player": "kodik", "player_label": "Kodik", "group": "",
              "text": "1 серия", "heights": [720], "state": "playable", "published": None, "link": "u"}]}


def card_of(payload: dict) -> dict:
    return {1: VIDEO, 2: TORRENT}[payload["series_id"]]


def topics(spy) -> list[str]:
    return [env.topic for env in published(spy)]


# --- the composition ---------------------------------------------------------------------------------


async def test_the_composition_of_a_video_series_is_the_layout_of_composition(tmp_path):
    answers = {"catalog.series.get": card_of, "composition.get": LAYOUT, "torrents.composition": []}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        response = await client.get("/api/series/1/composition")
    assert response.status_code == 200
    assert CompositionReply.model_validate(response.json()) == CompositionReply.model_validate(LAYOUT)
    assert owners.asked("composition.get") == [{"series_id": 1, "include_excluded": False, "sources_only": False}]
    assert owners.asked("torrents.composition") == [] and owners.asked("scan.series.prepare") == []
    # Nothing is merged in: the layout is asked for alone, the references have their own point.
    assert [t for t in topics(spy) if t not in ("catalog.series.get", "composition.get")] == []


async def test_the_rows_the_constructor_excluded_come_only_when_asked_for(tmp_path):
    answers = {"catalog.series.get": card_of, "composition.get": LAYOUT}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        await client.get("/api/series/1/composition?include_excluded=true")
    assert owners.asked("composition.get") == [{"series_id": 1, "include_excluded": True, "sources_only": False}]


async def test_refresh_does_not_start_a_scan_of_a_video_series(tmp_path):
    answers = {"catalog.series.get": card_of, "composition.get": LAYOUT, "scan.series.run": {},
               "scan.series.prepare": {}}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        assert (await client.get("/api/series/1/composition?refresh=true")).status_code == 200
    assert owners.asked("scan.series.run") == [] and owners.asked("scan.series.prepare") == []
    assert "scan.series.run" not in topics(spy)


async def test_a_scan_of_a_series_downloads_unless_the_window_asks_for_the_status_alone(tmp_path):
    answers = {"scan.series.run": {"changed": False}}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        assert (await client.post("/api/series/1/scan")).status_code == 200
        assert (await client.post("/api/series/1/scan?download=false")).status_code == 200
    assert owners.asked("scan.series.run") == [{"series_id": 1}, {"series_id": 1, "download": False}]


async def test_the_reset_of_failed_attempts_is_a_change_of_the_series_and_starts_no_sync(tmp_path):
    """The button «Сбросить неудачные попытки»: the plan forgets what failed, inside the admission of a change of the
    series; no sync is asked for (the work goes on with the next one)."""
    answers = {"planner.failures.forget": {"reset": 3}}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        response = await client.post("/api/series/1/failures/reset")
    assert response.status_code == 200 and response.json() == {"success": True, "reset": 3}
    assert owners.asked("planner.failures.forget") == [{"series_id": 1}]
    assert owners.asked("scan.series.run") == []
    assert topics(spy) == ["scan.edit.begin", "planner.failures.forget", "scan.edit.end"]


async def test_the_composition_of_a_torrent_series_is_the_torrent_plan_with_iso_dates(tmp_path):
    plan = [{"id": 1, "original_path": "Dir/T.S01E01.mkv", "source_data": {"publication_date": "2026-01-01 00:00:00"}}]
    answers = {"catalog.series.get": card_of, "torrents.composition": plan, "composition.get": LAYOUT}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        response = await client.get("/api/series/2/composition")
    assert response.json()[0]["source_data"]["publication_date"] == "2026-01-01T00:00:00"
    assert owners.asked("torrents.composition") == [{"series_id": 2, "refresh": False}]
    assert owners.asked("composition.get") == []


async def test_refresh_of_a_torrent_series_fetches_the_release_before_the_plan(tmp_path):
    answers = {"catalog.series.get": card_of, "scan.series.prepare": {"tasks_created": 1},
               "torrents.composition": []}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        assert (await client.get("/api/series/2/composition?refresh=true")).status_code == 200
    assert [t for t, _ in owners.calls] == ["catalog.series.get", "scan.series.prepare", "torrents.composition"]
    assert owners.asked("torrents.composition") == [{"series_id": 2, "refresh": True}]


async def test_the_composition_of_an_unknown_series_is_not_found(tmp_path):
    async with serving(tmp_path, {"catalog.series.get": LookupError("сериал 9 не найден")}) as (client, _, _, _):
        response = await client.get("/api/series/9/composition")
    assert response.status_code == 404 and response.json() == {"error": "Сериал не найден"}


# --- the references ----------------------------------------------------------------------------------


async def test_the_output_is_the_references_of_the_plan_as_they_are(tmp_path):
    references = [
        {"season": 1, "episode": 1, "part": None, "mark": "by_level", "source_id": 4, "row_key": "r1",
         "player": "kodik", "voice_group": "Group", "height": 720, "reason": None, "compilation": None},
        {"season": 1, "episode": 2, "part": None, "mark": "none", "source_id": None, "row_key": None,
         "player": None, "voice_group": None, "height": None, "reason": reason("reference_none"), "compilation": None}]
    async with serving(tmp_path, {"planner.references.get": references}) as (client, owners, _, spy):
        response = await client.get("/api/series/1/output")
    assert response.status_code == 200 and response.json() == references
    assert [Reference.model_validate(each).mark.value for each in response.json()] == ["by_level", "none"]
    assert owners.asked("planner.references.get") == [{"series_id": 1}]
    # A mark that reads "other player" or "none" is told by the plan alone: no other owner is asked to explain it.
    assert topics(spy) == ["planner.references.get"]


async def test_an_output_the_planner_refuses_to_give_is_a_refusal(tmp_path):
    async with serving(tmp_path, {"planner.references.get": LookupError("сериал 9 не найден")}) as (client, _, _, _):
        response = await client.get("/api/series/9/output")
    assert response.status_code == 404 and "success" not in response.json()


# --- the levers --------------------------------------------------------------------------------------


async def test_a_lever_is_set_with_the_series_of_the_path_and_the_body_as_it_came(tmp_path):
    async with serving(tmp_path, {"composition.settings.set": {"ok": True}}) as (client, owners, _, _):
        response = await client.put("/api/series/1/output/settings", json={
            "season": 1, "episode": 2, "lever": "voice.take", "target": "group:Group", "value": False})
    assert response.status_code == 200 and response.json() == {"success": True}
    assert owners.asked("composition.settings.set") == [{
        "series_id": 1, "season": 1, "episode": 2, "compilation": None, "lever": "voice.take", "target": "group:Group",
        "value": False}]


async def test_a_lever_of_the_series_level_needs_no_season(tmp_path):
    async with serving(tmp_path, {"composition.settings.set": {"ok": True}}) as (client, owners, _, _):
        await client.put("/api/series/1/output/settings", json={"lever": "reference.order", "value": []})
    assert owners.asked("composition.settings.set") == [{
        "series_id": 1, "season": None, "episode": None, "compilation": None, "lever": "reference.order", "target": None,
        "value": []}]


@pytest.mark.parametrize("code", ["unknown_lever", "bad_target", "bad_value"])
async def test_a_lever_the_registry_refuses_is_a_400_with_the_code(tmp_path, code):
    refusal = ValueError(reason(code, lever="x"))
    async with serving(tmp_path, {"composition.settings.set": refusal}) as (client, _, _, _):
        response = await client.put("/api/series/1/output/settings", json={"lever": "x", "value": 1})
    assert response.status_code == 400 and response.json()["success"] is False
    assert parse(response.json()["error"]) == {"code": code, "params": {"lever": "x"}}


async def test_an_episode_without_a_season_is_a_malformed_request_and_asks_nobody(tmp_path):
    async with serving(tmp_path, {"composition.settings.set": {"ok": True}}) as (client, owners, _, spy):
        response = await client.put("/api/series/1/output/settings", json={"episode": 2, "lever": "voice.take"})
    assert response.status_code == 422
    assert owners.asked("composition.settings.set") == [] and topics(spy) == []


async def test_a_body_with_a_field_the_command_lacks_is_refused_by_the_model(tmp_path):
    async with serving(tmp_path, {"composition.settings.set": {"ok": True}}) as (client, owners, _, _):
        response = await client.put("/api/series/1/output/settings", json={"lever": "voice.take", "series_id": 2})
    assert response.status_code == 422 and owners.asked("composition.settings.set") == []


async def test_a_level_is_reset_with_the_series_of_the_path(tmp_path):
    async with serving(tmp_path, {"composition.settings.reset": {"ok": True}}) as (client, owners, _, _):
        response = await client.post("/api/series/1/output/settings/reset", json={"season": 2})
        await client.post("/api/series/1/output/settings/reset", json={})
        await client.post("/api/series/1/output/settings/reset", json={
            "season": 2, "episode": 3, "lever": "voice.order", "target": "group:Group"})
    assert response.status_code == 200 and response.json() == {"success": True}
    assert owners.asked("composition.settings.reset") == [
        {"series_id": 1, "season": 2, "episode": None, "compilation": None, "lever": None, "target": None},
        {"series_id": 1, "season": None, "episode": None, "compilation": None, "lever": None, "target": None},
        {"series_id": 1, "season": 2, "episode": 3, "compilation": None, "lever": "voice.order", "target": "group:Group"}]


async def test_a_reset_the_registry_refuses_is_a_400_with_the_code(tmp_path):
    refusal = ValueError(reason("unknown_lever", lever="x"))
    async with serving(tmp_path, {"composition.settings.reset": refusal}) as (client, _, _, _):
        response = await client.post("/api/series/1/output/settings/reset", json={"lever": "x"})
    assert response.status_code == 400 and parse(response.json()["error"])["code"] == "unknown_lever"


# --- the switch of an episode or a season -------------------------------------------------------------


async def test_an_episode_and_a_season_are_switched_with_the_series_of_the_path(tmp_path):
    async with serving(tmp_path, {"scan.output.switch": {"success": True}}) as (client, owners, _, _):
        episode = await client.put("/api/series/1/output/switch", json={"season": 2, "episode": 3, "off": True})
        await client.put("/api/series/1/output/switch", json={"season": 2, "off": False})
    assert episode.status_code == 200 and episode.json() == {"success": True}
    assert owners.asked("scan.output.switch") == [
        {"series_id": 1, "season": 2, "episode": 3, "off": True},
        {"series_id": 1, "season": 2, "episode": None, "off": False}]


async def test_a_switch_without_its_answer_or_with_a_foreign_field_is_a_malformed_request_and_asks_nobody(tmp_path):
    async with serving(tmp_path, {"scan.output.switch": {"success": True}}) as (client, owners, _, _):
        for body in ({"season": 1}, {"off": True}, {"season": 1, "off": True, "series_id": 2}):
            assert (await client.put("/api/series/1/output/switch", json=body)).status_code == 422
    assert owners.asked("scan.output.switch") == []


async def test_a_series_scan_does_not_have_is_a_404_for_a_switch(tmp_path):
    answers = {"scan.output.switch": LookupError("сериал 9 не найден")}
    async with serving(tmp_path, answers) as (client, _, _, _):
        response = await client.put("/api/series/9/output/switch", json={"season": 1, "off": True})
    assert response.status_code == 404


async def test_a_command_to_a_module_that_is_not_there_is_a_503(tmp_path):
    async with serving(tmp_path, {}, watch=False) as (client, _, _, _):
        response = await client.put("/api/series/1/output/settings", json={"lever": "voice.take", "value": True})
    assert response.status_code == 503


# --- the bodies keep the fields of the commands -------------------------------------------------------


def test_the_body_of_a_lever_has_the_fields_of_the_command_but_the_series():
    assert set(OutputSettingsSet.model_fields) == set(SettingsSet.model_fields) - {"series_id"}
    assert set(OutputSettingsReset.model_fields) == set(SettingsReset.model_fields) - {"series_id"}
    assert set(OutputSwitchBody.model_fields) == set(OutputSwitch.model_fields) - {"series_id"}


async def test_a_request_for_the_sources_only_reaches_composition_as_it_is(tmp_path):
    answers = {"catalog.series.get": card_of, "composition.get": LAYOUT}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        await client.get("/api/series/1/composition?sources_only=true")
    assert owners.asked("composition.get") == [{"series_id": 1, "include_excluded": False, "sources_only": True}]



async def test_the_queues_of_a_level_are_the_answer_of_composition(tmp_path):
    queues = {"series_id": 1, "season": 4, "episode": 11, "compilation": None, "reference": {"rows": [], "order_level": "default"},
              "groups": []}
    async with serving(tmp_path, {"composition.queues.get": queues}) as (client, owners, _, spy):
        response = await client.get("/api/series/1/output/queues", params={"season": 4, "episode": 11})
        alone = await client.get("/api/series/1/output/queues", params={"episode": 11})
    assert response.status_code == 200 and response.json() == queues
    assert owners.asked("composition.queues.get") == [{"series_id": 1, "season": 4, "episode": 11, "compilation": None}]
    assert alone.status_code == 422  # an episode is told within its season


async def test_the_command_delete_goes_to_the_planner_and_starts_no_sync(tmp_path):
    """«Удалить» of a file or of a track in it, and the command taken back: each is one command of the planner, carried
    out by the next sync."""
    answers = {"planner.delete": {"recorded": True}, "planner.delete.cancel": {"dropped": True}}
    dub = {"source_id": 2, "row_key": "g1", "video": "v1"}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        video = await client.post("/api/series/1/plan/delete", json={"kind": "video", "name": "s01e01.mkv"})
        track = await client.post("/api/series/1/plan/delete", json={"kind": "track", "name": "s01e01.mkv", "dub": dub})
        back = await client.post("/api/series/1/plan/delete/cancel", json={"kind": "video", "name": "s01e01.mkv"})
    assert all(each.status_code == 200 and each.json()["success"] for each in (video, track, back))
    assert owners.asked("planner.delete") == [
        {"series_id": 1, "kind": "video", "name": "s01e01.mkv", "dub": None},
        {"series_id": 1, "kind": "track", "name": "s01e01.mkv", "dub": dub}]
    assert owners.asked("planner.delete.cancel") == [{"series_id": 1, "kind": "video", "name": "s01e01.mkv", "dub": None}]
    assert owners.asked("scan.series.run") == []


async def test_a_refusal_of_the_planner_is_its_answer(tmp_path):
    def refuse(payload):
        raise ValueError("planner_delete_no_file")
    async with serving(tmp_path, {"planner.delete": refuse}) as (client, owners, _, spy):
        response = await client.post("/api/series/1/plan/delete", json={"kind": "video", "name": "gone.mkv"})
    assert response.status_code == 400 and "planner_delete_no_file" in response.json()["error"]
