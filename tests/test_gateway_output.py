"""The gateway points of the output section: the composition of a series, its references and the levers.
Each answer is one owner's answer as it is; the gateway merges nothing and knows nothing of sites or players."""

from core.reasons import parse, reason
from gateway_fakes import published, serving

VIDEO = {"id": 1, "sources": []}
TORRENT = {"id": 2, "sources": [{"id": 5, "url": "https://kinozal.tv/details.php?id=1", "source_type": "torrent"}]}
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


# --- the references ----------------------------------------------------------------------------------


async def test_an_output_the_planner_refuses_to_give_is_a_refusal(tmp_path):
    async with serving(tmp_path, {"planner.references.get": LookupError("сериал 9 не найден")}) as (client, _, _, _):
        response = await client.get("/api/series/9/output")
    assert response.status_code == 404 and "success" not in response.json()


# --- the levers --------------------------------------------------------------------------------------


async def test_levers_the_registry_refuses_in_a_batch_are_a_400_with_the_code(tmp_path):
    refusal = ValueError(reason("bad_value", lever="reference.allow"))
    async with serving(tmp_path, {"composition.settings.set_many": refusal}) as (client, _, _, _):
        response = await client.put("/api/series/1/output/settings/batch", json={"items": [
            {"lever": "reference.allow", "value": 1}]})
    assert response.status_code == 400 and response.json()["success"] is False
    assert parse(response.json()["error"]) == {"code": "bad_value", "params": {"lever": "reference.allow"}}


async def test_the_name_of_a_track_is_set_with_the_series_of_the_path_on_one_line(tmp_path):
    async with serving(tmp_path, {"composition.voice_title.set": {"ok": True}}) as (client, owners, _, _):
        response = await client.put("/api/series/1/output/voice-title", json={"label": "AniLiberty", "title": " ПМ_aniliberty "})
        assert response.status_code == 200 and response.json() == {"success": True}
        broken = await client.put("/api/series/1/output/voice-title", json={"label": "AniLiberty", "title": "две\nстроки"})
        assert broken.status_code == 422
    assert owners.asked("composition.voice_title.set") == [
        {"series_id": 1, "label": "AniLiberty", "title": "ПМ_aniliberty"}]


async def test_a_reset_the_registry_refuses_is_a_400_with_the_code(tmp_path):
    refusal = ValueError(reason("unknown_lever", lever="x"))
    async with serving(tmp_path, {"composition.settings.reset": refusal}) as (client, _, _, _):
        response = await client.post("/api/series/1/output/settings/reset", json={"lever": "x"})
    assert response.status_code == 400 and parse(response.json()["error"])["code"] == "unknown_lever"


# --- the switch of an episode or a season -------------------------------------------------------------


async def test_a_command_to_a_module_that_is_not_there_is_a_503(tmp_path):
    async with serving(tmp_path, {}, watch=False) as (client, _, _, _):
        response = await client.put("/api/series/1/output/settings", json={"lever": "voice.take", "value": True})
    assert response.status_code == 503


# --- the bodies keep the fields of the commands -------------------------------------------------------


async def test_a_request_for_the_sources_only_reaches_composition_as_it_is(tmp_path):
    answers = {"catalog.series.get": card_of, "composition.get": LAYOUT}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        await client.get("/api/series/1/composition?sources_only=true")
    assert owners.asked("composition.get") == [{"series_id": 1, "include_excluded": False, "sources_only": True, "part": "whole", "episodes": []}]



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
