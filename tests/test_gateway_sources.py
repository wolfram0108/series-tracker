"""The gateway points of the sources: the sources of a series, the pages that are no source yet, the test of rules
and the parse of a torrent page. Each point carries the exact body to the owner, carries a refusal back with its
code, and knows no site: what an address is, is the answer of the owners."""
import asyncio

import pytest

from core.contracts import SeriesSource
from core.reasons import parse, reason
from gateway_fakes import published, serving

SOURCE = {"id": 4, "series_id": 1, "position": 0, "url": "https://x.test/p", "source_type": "web_video",
          "parser_profile_id": 2, "vibix_season": None, "players_off": []}
PREVIEW = {"url": "https://x.test/p", "state": "ready", "reason": None, "summary": None, "lines": [], "rows": []}
# The choices of a source the preview carries beside the address; a window that sends none asks for the defaults.
NO_CHOICES = {"players_off": [], "vibix_season": None, "voice_name": ""}


def code_of(response) -> str:
    return parse(response.json()["error"])["code"]


def topics(spy) -> list[str]:
    return [env.topic for env in published(spy)]


# --- the sources of a series -------------------------------------------------------------------


async def test_the_sources_of_a_series_are_the_catalogs_list(tmp_path):
    async with serving(tmp_path, {"catalog.sources.list": [SOURCE]}) as (client, owners, _, _):
        response = await client.get("/api/series/1/sources")
    assert response.status_code == 200
    assert [SeriesSource.model_validate(each) for each in response.json()] == [SeriesSource.model_validate(SOURCE)]
    assert owners.asked("catalog.sources.list") == [{"series_id": 1}]


async def test_the_sources_of_a_series_the_catalog_does_not_have_are_not_found(tmp_path):
    missing = LookupError("сериал 7 не найден")
    async with serving(tmp_path, {"catalog.sources.list": missing}) as (client, _, _, _):
        assert (await client.get("/api/series/7/sources")).status_code == 404


async def test_a_source_is_added_with_exactly_the_fields_the_caller_gave(tmp_path):
    async with serving(tmp_path, {"catalog.sources.add": {"id": 12}}) as (client, owners, _, _):
        response = await client.post("/api/series/1/sources", json={
            "url": "https://x.test/p", "source_type": "web_video", "parser_profile_id": 2, "players_off": ["vk"]})
    assert response.status_code == 200 and response.json() == {"id": 12}
    # Defaults are the catalog's to fill: a field the caller did not send is not sent on.
    assert owners.asked("catalog.sources.add") == [{"series_id": 1, "source": {
        "url": "https://x.test/p", "source_type": "web_video", "parser_profile_id": 2, "players_off": ["vk"]}}]


async def test_a_source_is_added_at_a_position_when_the_caller_names_one(tmp_path):
    async with serving(tmp_path, {"catalog.sources.add": {"id": 12}}) as (client, owners, _, _):
        await client.post("/api/series/1/sources", json={
            "url": "https://x.test/p", "source_type": "vk_video", "position": 0})
    assert owners.asked("catalog.sources.add")[0]["source"]["position"] == 0


@pytest.mark.parametrize("refusal,status", [
    (ValueError(reason("source_exists", url="https://x.test/p")), 400),
    (ValueError(reason("profile_not_found", profile_id=99)), 400),
    (LookupError("сериал 1 не найден"), 404)])
async def test_a_refusal_of_the_catalog_reaches_the_caller_with_its_code(tmp_path, refusal, status):
    async with serving(tmp_path, {"catalog.sources.add": refusal}) as (client, _, _, _):
        response = await client.post("/api/series/1/sources", json={"url": "u", "source_type": "web_video"})
    assert response.status_code == status and response.json()["success"] is False
    if status == 400:
        assert code_of(response) in ("source_exists", "profile_not_found")


async def test_a_source_of_an_unknown_kind_is_a_malformed_request(tmp_path):
    async with serving(tmp_path, {"catalog.sources.add": {"id": 1}}) as (client, owners, _, _):
        response = await client.post("/api/series/1/sources", json={"url": "u", "source_type": "telegram"})
    assert response.status_code == 422 and owners.asked("catalog.sources.add") == []


async def test_only_the_fields_sent_change_and_a_choice_can_be_taken_back(tmp_path):
    answers = {"catalog.sources.update": {"applied": {"parser_profile_id": None}}}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        response = await client.put("/api/series/1/sources/4", json={"parser_profile_id": None})
    assert response.status_code == 200 and response.json() == {"success": True, "applied": {"parser_profile_id": None}}
    # The series of the path goes to the catalog, which refuses a source of another series; the gateway asks nothing else
    # than that, inside the admission of the change by the owner of the runs of the series.
    assert owners.asked("catalog.sources.update") == [{
        "series_id": 1, "source_id": 4, "fields": {"parser_profile_id": None}}]
    assert topics(spy) == ["scan.edit.begin", "catalog.sources.update", "scan.edit.end"]


async def test_every_changeable_field_of_a_source_reaches_the_catalog(tmp_path):
    fields = {"url": "https://x.test/q", "parser_profile_id": 3, "vibix_season": 2,
              "players_off": ["vk", "kodik"], "position": 1}
    async with serving(tmp_path, {"catalog.sources.update": {"applied": fields}}) as (client, owners, _, _):
        assert (await client.put("/api/series/1/sources/4", json=fields)).status_code == 200
    assert owners.asked("catalog.sources.update") == [{"series_id": 1, "source_id": 4, "fields": fields}]


@pytest.mark.parametrize("body", [{"source_type": "torrent"}, {"series_id": 2}, {"url": None}, {"position": None},
                                  {"players_off": None}])
async def test_what_a_source_does_not_let_change_is_refused_by_the_model(tmp_path, body):
    async with serving(tmp_path, {"catalog.sources.update": {"applied": {}}}) as (client, owners, _, _):
        response = await client.put("/api/series/1/sources/4", json=body)
    assert response.status_code == 422 and owners.asked("catalog.sources.update") == []


async def test_a_source_of_another_series_is_not_found_under_this_one_by_the_catalog(tmp_path):
    refusal = LookupError("источник 9 не найден у сериала 1")
    answers = {"catalog.sources.update": refusal, "catalog.sources.remove": refusal}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        changed = await client.put("/api/series/1/sources/9", json={"vibix_season": 2})
        removed = await client.delete("/api/series/1/sources/9")
    for response in (changed, removed):
        assert response.status_code == 404 and response.json() == {
            "success": False, "error": "источник 9 не найден у сериала 1"}
    # Whose source it is, is the catalog's to say: both commands carry the series of the path.
    assert owners.asked("catalog.sources.update")[0]["series_id"] == 1
    assert owners.asked("catalog.sources.remove") == [{"series_id": 1, "source_id": 9}]
    assert "catalog.sources.list" not in topics(spy)


async def test_a_source_is_removed_by_its_id(tmp_path):
    async with serving(tmp_path, {"catalog.sources.remove": {"ok": True}}) as (client, owners, _, _):
        response = await client.delete("/api/series/1/sources/4")
    assert response.status_code == 200 and response.json() == {"success": True}
    assert owners.asked("catalog.sources.remove") == [{"series_id": 1, "source_id": 4}]


async def test_the_last_source_of_a_series_is_refused_with_its_code(tmp_path):
    refusal = ValueError(reason("last_source", series_id=1, source_id=4))
    async with serving(tmp_path, {"catalog.sources.remove": refusal}) as (client, _, _, _):
        response = await client.delete("/api/series/1/sources/4")
    assert response.status_code == 400 and code_of(response) == "last_source"
    assert parse(response.json()["error"])["params"] == {"series_id": 1, "source_id": 4}


# --- a page that is no source yet --------------------------------------------------------------


async def test_a_preview_carries_the_request_to_composition_whole_and_the_answer_back(tmp_path):
    reply = {**PREVIEW, "state": "reading", "names": [], "lines": [{"key": "k1", "line": "Т | VK | Серия 1", "result": None, "heights": []}]}
    async with serving(tmp_path, {"composition.preview": reply}) as (client, owners, _, spy):
        response = await client.post("/api/sources/preview", json={
            "url": "https://x.test/p", "profile_id": 2, "series_season": "s02", "refresh": False})
    assert response.status_code == 200 and response.json() == reply
    assert owners.asked("composition.preview") == [{
        "url": "https://x.test/p", "profile_id": 2, "series_season": "s02", "refresh": False, "lines": None,
        **NO_CHOICES}]
    # The gateway reads no table and applies no rule itself.
    assert [t for t in topics(spy) if t != "composition.preview"] == []


async def test_a_preview_without_a_profile_asks_for_a_read_by_default(tmp_path):
    async with serving(tmp_path, {"composition.preview": PREVIEW}) as (client, owners, _, _):
        await client.post("/api/sources/preview", json={"url": "https://x.test/p"})
    assert owners.asked("composition.preview") == [{
        "url": "https://x.test/p", "profile_id": None, "series_season": None, "refresh": True,
        "lines": None, **NO_CHOICES}]


async def test_a_preview_refused_by_composition_carries_the_reason(tmp_path):
    refusal = ValueError(reason("composition_request_failed", topic="sources.table.get"))
    async with serving(tmp_path, {"composition.preview": refusal}) as (client, _, _, _):
        response = await client.post("/api/sources/preview", json={"url": "https://x.test/p"})
    assert response.status_code == 400 and code_of(response) == "composition_request_failed"
    assert "success" not in response.json()


async def test_an_address_is_resolved_by_sources_and_the_answer_is_theirs(tmp_path):
    answers = {"sources.site.resolve": {"site": "somesite", "canonical_url": "https://x.test/p",
                                        "source_type": "web_video"}}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        response = await client.post("/api/sources/resolve", json={"url": "https://x.test/p?a=1"})
    assert response.json() == {"site": "somesite", "canonical_url": "https://x.test/p", "source_type": "web_video"}
    assert owners.asked("sources.site.resolve") == [{"url": "https://x.test/p?a=1"}]
    assert [t for t in topics(spy) if t != "sources.site.resolve"] == []


async def test_an_address_no_site_reads_is_resolved_to_nothing(tmp_path):
    async with serving(tmp_path, {"sources.site.resolve": {"site": None, "canonical_url": None}}
                       ) as (client, _, _, _):
        response = await client.post("/api/sources/resolve", json={"url": "http://kinozal.tv/details.php?id=1"})
    assert response.json() == {"site": None, "canonical_url": None, "source_type": None}


async def test_a_page_is_read_now_by_one_command_to_sources(tmp_path):
    async with serving(tmp_path, {}) as (client, owners, _, spy):
        response = await client.post("/api/sources/read", json={"url": "https://x.test/p"})
        await asyncio.sleep(0)
    assert response.status_code == 200 and response.json()["success"] is True
    sent = [env for env in published(spy) if env.topic == "sources.table.read"]
    assert [env.payload for env in sent] == [{"url": "https://x.test/p"}]


# --- the test of rules ---------------------------------------------------------------------------


async def test_the_test_of_rules_is_the_preview_of_the_page_with_the_profile_given(tmp_path):
    line = {"key": "k1", "line": "Т | Kodik | 1-3 серия", "result": {"excluded": False, "start": 1, "end": 3,
                                                                        "events": [], "errors": []}}
    reply = {**PREVIEW, "lines": [line]}
    async with serving(tmp_path, {"composition.preview": reply}) as (client, owners, _, spy):
        response = await client.post("/api/parser-profiles/test", json={
            "url": "https://x.test/p", "profile_id": 5, "series_season": 2})
    assert response.status_code == 200
    assert response.json()["lines"][0]["key"] == "k1" and response.json()["lines"][0]["result"]["start"] == 1
    assert owners.asked("composition.preview") == [{
        "url": "https://x.test/p", "profile_id": 5, "series_season": 2,
        "refresh": True, "lines": None, **NO_CHOICES}]
    assert "rules.test" not in topics(spy) and "rules.apply" not in topics(spy)


async def test_the_test_of_rules_takes_lines_the_user_holds_instead_of_a_page(tmp_path):
    line = {"key": "0", "line": "Тайтл s01e02", "result": {"excluded": False, "season": 1, "episode": 2,
                                                          "events": [], "errors": []}}
    async with serving(tmp_path, {"composition.preview": {**PREVIEW, "url": "", "lines": [line]}}) as (client, owners, _, _):
        response = await client.post("/api/parser-profiles/test", json={"profile_id": 5, "lines": ["Тайтл s01e02"]})
    assert response.status_code == 200 and response.json()["lines"][0]["result"]["episode"] == 2
    assert owners.asked("composition.preview") == [{
        "url": "", "profile_id": 5, "series_season": None, "refresh": True, "lines": ["Тайтл s01e02"],
        **NO_CHOICES}]


async def test_the_test_of_rules_asks_for_no_read_when_the_window_only_takes_the_table(tmp_path):
    async with serving(tmp_path, {"composition.preview": PREVIEW}) as (client, owners, _, _):
        await client.post("/api/parser-profiles/test", json={"url": "https://x.test/p", "profile_id": 5, "refresh": False})
    assert owners.asked("composition.preview")[0]["refresh"] is False


async def test_the_test_of_rules_needs_a_profile_and_a_page(tmp_path):
    async with serving(tmp_path, {"composition.preview": PREVIEW}) as (client, owners, _, _):
        for body in ({"url": "https://x.test/p"}, {"profile_id": 5}, {"url": "u", "profile_id": 5, "videos": []},
                     {"lines": ["x"]}):
            assert (await client.post("/api/parser-profiles/test", json=body)).status_code == 422
    assert owners.asked("composition.preview") == []


# --- the torrent page of the add form -------------------------------------------------------------

TORRENT_PAGE = {"title": {"ru": "Тайтл", "en": "Title"}, "tracker": {"canonical_name": "kinozal"},
                "releases": [{"link": "http://kinozal.tv/dl/1", "magnet": None, "date_marker": "2026-01-01 00:00",
                              "torrent_id": "t1"},
                             {"link": None, "magnet": "magnet:?xt=1", "date_marker": "2026-01-02 00:00"}]}


async def test_a_torrent_page_is_parsed_after_sources_said_no_site_reads_it(tmp_path):
    answers = {"sources.site.resolve": {"site": None, "canonical_url": None}, "sources.parse": TORRENT_PAGE}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        response = await client.post("/api/parse_url", json={"url": "http://kinozal.tv/details.php?id=1"})
    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    assert body["title"] == {"ru": "Тайтл", "en": "Title"} and body["tracker_info"] == {"canonical_name": "kinozal"}
    assert [(each["link"], each["date_time"]) for each in body["torrents"]] == [
        ("http://kinozal.tv/dl/1", "2026-01-01 00:00"), ("magnet:?xt=1", "2026-01-02 00:00")]
    assert owners.asked("sources.parse") == [{"url": "http://kinozal.tv/details.php?id=1"}]
    # What a site reads is not in this answer any more: no layout, no players, no sources of a web page.
    assert not {"sources", "layout", "has_layout", "players", "selected_source", "source_type"} & set(body)


async def test_a_page_a_site_reads_is_not_a_torrent_page_and_is_not_parsed(tmp_path):
    answers = {"sources.site.resolve": {"site": "somesite", "canonical_url": "https://x.test/p"},
               "sources.parse": TORRENT_PAGE}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        response = await client.post("/api/parse_url", json={"url": "https://x.test/p"})
    assert response.status_code == 400 and code_of(response) == "not_a_torrent_page"
    assert owners.asked("sources.parse") == []


async def test_an_address_no_tracker_knows_is_refused_with_the_words_of_sources(tmp_path):
    answers = {"sources.site.resolve": {"site": None, "canonical_url": None},
               "sources.parse": ValueError("трекер не распознан по URL: http://nowhere.test/")}
    async with serving(tmp_path, answers) as (client, _, _, _):
        response = await client.post("/api/parse_url", json={"url": "http://nowhere.test/"})
    assert response.status_code == 400
    assert response.json() == {"error": "трекер не распознан по URL: http://nowhere.test/"}


# --- the naming preview of the add window ---------------------------------------------------------


async def test_the_naming_preview_is_one_command_to_torrents_and_the_gateway_publishes_no_outcome(tmp_path):
    items = [{"torrent_id": "t1", "link": "https://t.test/1.torrent"}, {"torrent_id": 2, "magnet": "magnet:?xt=urn:btih:ab"}]
    async with serving(tmp_path, {"torrents.preview.fetch": None}) as (client, owners, _, spy):
        response = await client.post("/api/preview/naming/fetch", json={"items": items})
        sent = await owners.next("torrents.preview.fetch")
        seen = published(spy)
    assert response.status_code == 200 and response.json() == {"accepted": ["t1", 2]}
    assert sent == {"items": items}
    # One command and nothing else: no request to sources, no step per release, no event of torrents.
    assert [(env.kind, env.topic) for env in seen] == [("command", "torrents.preview.fetch")]


async def test_a_release_with_a_field_the_preview_does_not_know_is_refused(tmp_path):
    async with serving(tmp_path, {"torrents.preview.fetch": None}) as (client, owners, _, _):
        response = await client.post("/api/preview/naming/fetch", json={"items": [{"torrent_id": "t", "url": "x"}]})
    assert response.status_code == 422 and owners.asked("torrents.preview.fetch") == []


# --- what is gone ---------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("post", "/api/parser-profiles/scrape-titles"), ("post", "/api/web_video/coverage"),
    ("post", "/api/web_video/layout"), ("put", "/api/series/1/vk-quality-priority")])
async def test_the_points_of_the_old_reading_are_gone(tmp_path, method, path):
    async with serving(tmp_path, {}) as (client, _, _, _):
        response = await getattr(client, method)(path, json={})
    assert response.status_code in (404, 405)


async def test_a_change_of_a_source_while_a_sync_of_the_series_runs_is_refused_before_anything_is_written(tmp_path):
    """Н-8: the owner of the runs refuses to admit the change; the catalog is not asked."""
    from core.contracts.availability import Action, NotAllowed, refusal
    answers = {"scan.edit.begin": NotAllowed(refusal(Action.EDIT, "scanning")), "catalog.sources.update": {"applied": {}}}
    async with serving(tmp_path, answers) as (client, owners, _, spy):
        response = await client.put("/api/series/1/sources/4", json={"parser_profile_id": None})
    assert response.status_code == 409 and owners.asked("catalog.sources.update") == []
