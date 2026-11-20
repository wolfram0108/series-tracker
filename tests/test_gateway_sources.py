"""The gateway points of the sources: the sources of a series, the pages that are no source yet, the test of rules
and the parse of a torrent page. Each point carries the exact body to the owner, carries a refusal back with its
code, and knows no site: what an address is, is the answer of the owners."""


from core.reasons import parse, reason
from gateway_fakes import published, serving

SOURCE = {"id": 4, "series_id": 1, "position": 0, "url": "https://x.test/p", "source_type": "web_video",
          "parser_profile_id": 2, "vibix_season": None, "players_off": []}
PREVIEW = {"url": "https://x.test/p", "state": "ready", "reason": None, "summary": None, "lines": [], "rows": []}
# The choices of a source the preview carries beside the address; a window that sends none asks for the defaults.
def code_of(response) -> str:
    return parse(response.json()["error"])["code"]


def topics(spy) -> list[str]:
    return [env.topic for env in published(spy)]


# --- the sources of a series -------------------------------------------------------------------


async def test_a_source_is_added_at_a_position_when_the_caller_names_one(tmp_path):
    async with serving(tmp_path, {"catalog.sources.add": {"id": 12}}) as (client, owners, _, _):
        await client.post("/api/series/1/sources", json={
            "url": "https://x.test/p", "source_type": "vk_video", "position": 0})
    assert owners.asked("catalog.sources.add")[0]["source"]["position"] == 0


async def test_a_source_of_another_series_is_not_found_under_this_one_by_the_catalog(tmp_path):
    refusal = LookupError(reason("source_not_in_series", source_id=9, series_id=1))
    answers = {"catalog.sources.update": refusal, "catalog.sources.remove": refusal}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        changed = await client.put("/api/series/1/sources/9", json={"vibix_season": 2})
        removed = await client.delete("/api/series/1/sources/9")
    for response in (changed, removed):
        assert response.status_code == 404 and response.json() == {
            "success": False, "error": reason("source_not_in_series", source_id=9, series_id=1)}
    # Whose source it is, is the catalog's to say: both commands carry the series of the path.
    assert owners.asked("catalog.sources.update")[0]["series_id"] == 1
    assert owners.asked("catalog.sources.remove") == [{"series_id": 1, "source_id": 9}]
    assert "catalog.sources.list" not in topics(spy)


# --- a page that is no source yet --------------------------------------------------------------


async def test_a_preview_refused_by_composition_carries_the_reason(tmp_path):
    refusal = ValueError(reason("composition_request_failed", topic="sources.table.get"))
    async with serving(tmp_path, {"composition.preview": refusal}) as (client, _, _, _):
        response = await client.post("/api/sources/preview", json={"url": "https://x.test/p"})
    assert response.status_code == 400 and code_of(response) == "composition_request_failed"
    assert "success" not in response.json()


# --- the test of rules ---------------------------------------------------------------------------


async def test_the_test_of_rules_asks_for_no_read_when_the_window_only_takes_the_table(tmp_path):
    async with serving(tmp_path, {"composition.preview": PREVIEW}) as (client, owners, _, _):
        await client.post("/api/parser-profiles/test", json={"url": "https://x.test/p", "profile_id": 5, "refresh": False})
    assert owners.asked("composition.preview")[0]["refresh"] is False


# --- what is gone ---------------------------------------------------------------------------------
