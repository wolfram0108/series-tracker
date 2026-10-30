import json

import pytest

from modules.sources.players.contract import Kind
from modules.sources.players.vk_files import (BATCH_LIMIT, ApiContext, api_context, call_form, chunks, embed_parts,
                                              files_of, page_files, pick_file, read_call, read_item, read_page,
                                              read_single)

VIDEO = ("-10", "20")
SHELL = ('<html><head><title>VK</title></head><body><script>window.init = {webToken: {"access_token": "tok-1", '
         '"expired_at": 4102444800, "expires": 600}, "apiConfigDomains": {"apiDomain": "api.vkvideo.ru"}};</script></body></html>')
OLD = ('<html><head><title>Video embed</title></head><body><script>\nvar playerParams = {"params": [{"vid": 20, '
       '"mp4_720": "https:\\/\\/cdn.test\\/720.mp4?a=1\\u0026b=2", "mp4_2160": "https:\\/\\/cdn.test\\/2160.mp4", '
       '"hls": "https:\\/\\/cdn.test\\/m.m3u8"}]};\n</script></body></html>')
PREFETCH = ('<html><head><title>VK</title></head><script>window.cur.apiPrefetchCache = [{"method": "video.get", "response": '
            '{"items": [{"owner_id": -10, "id": 99, "files": {"mp4_144": "https://cdn.test/other.mp4"}}, '
            '{"owner_id": -10, "id": 20, "files": {"mp4_480": "https://cdn.test/480.mp4", "mp4_1080": "https://cdn.test/1080.mp4"}}]}}];'
            '</script></html>')


@pytest.mark.parametrize("url,expected", [
    ("https://vkvideo.ru/video_ext.php?oid=-10&id=20&hash=abc", ("-10", "20", "abc")),
    ("https://vk.com/video_ext.php?oid=7&amp;id=9", ("7", "9", None)),
    ("https://example.com/video_ext.php?oid=7&id=9&hash=a", None),
    ("https://vkvideo.ru/video-10_20", None),
    ("https://vkvideo.ru/video_ext.php?oid=x&id=9", None),
    ("", None)])
def test_an_embed_address_gives_the_video_and_its_key(url, expected):
    parts = embed_parts(url)

    assert (parts and (parts.oid, parts.id, parts.hash)) == (expected or None)


def test_the_key_of_a_video_carries_its_hash_when_the_link_has_one():
    assert embed_parts("https://vkvideo.ru/video_ext.php?oid=-10&id=20&hash=abc").key == "-10_20_abc"
    assert embed_parts("https://vkvideo.ru/video_ext.php?oid=-10&id=20").key == "-10_20"


def test_more_than_fifty_videos_are_split_even_when_a_bigger_batch_is_asked():
    assert [len(part) for part in chunks(list(range(121)), 100)] == [50, 50, 21]
    assert [len(part) for part in chunks(list(range(121)))] == [BATCH_LIMIT, BATCH_LIMIT, 21]
    assert list(chunks([], 50)) == []


def test_the_token_and_the_api_host_are_taken_from_the_page_the_player_gives_itself():
    context = api_context(SHELL)

    assert (context.token, context.domain, context.expires_at) == ("tok-1", "api.vkvideo.ru", 4102444800.0)
    assert "tok-1" not in repr(context)


@pytest.mark.parametrize("page", [OLD, SHELL.replace("api.vkvideo.ru", "evil.example"), SHELL.replace('"access_token": "tok-1"', '"access_token": ""'),
                                  SHELL.replace("webToken", "other"), ""])
def test_a_page_without_a_token_or_with_a_foreign_api_host_gives_no_context(page):
    assert api_context(page) is None


def test_the_call_form_names_the_embed_and_the_site_that_shows_it():
    form = call_form(ApiContext("tok", "api.vkvideo.ru"), ["-1_2_h", "-1_3_k"], "1.ruchime.org")

    assert form == {"access_token": "tok", "v": "5.289", "videos": "-1_2_h,-1_3_k", "count": "2", "is_embed": "1",
                    "ref_domain": "1.ruchime.org"}


def test_the_answer_is_matched_by_owner_and_id_whatever_the_order_and_whatever_is_missing():
    text = json.dumps({"response": {"items": [{"owner_id": -10, "id": 22, "files": {}}, {"owner_id": -10, "id": 20, "files": {"mp4_720": "https://c/1"}}]}})

    called = read_call(text)

    assert set(called.items) == {("-10", "22"), ("-10", "20")}
    assert ("-10", "21") not in called.items


@pytest.mark.parametrize("text", ["", "<html>", "[]", '{"response": {}}', '{"response": {"items": "x"}}'])
def test_what_is_not_an_answer_of_the_api_reads_as_nothing(text):
    assert read_call(text) is None


def test_an_error_of_the_api_is_kept_with_its_code_and_a_rejected_token_is_told_apart():
    rejected = read_call('{"error": {"error_code": 5, "error_msg": "User authorization failed"}}')
    throttled = read_call('{"error": {"error_code": 6, "error_msg": "Too many requests"}}')

    assert (rejected.error[0], rejected.token_rejected, throttled.token_rejected) == (5, True, False)


def test_the_files_are_the_progressive_ones_of_both_player_formats_and_need_a_real_address():
    listed = {"mp4_144": "https://c/144", "url720": "http://c/720", "mp4_1080": "", "mp4_2160": "file:///x", "hls": "https://c/m.m3u8", "dash_sep": "https://c/d"}

    assert files_of(listed) == {144: "https://c/144", 720: "http://c/720"}
    assert files_of(None) == {}


def test_an_item_with_files_is_playable_and_lists_them_by_height():
    reading = read_item({"files": {"mp4_720": "https://c/720", "mp4_144": "https://c/144"}})

    assert (reading.kind, reading.resolutions, reading.files) == (Kind.PLAYABLE, (144, 720), ((144, "https://c/144"), (720, "https://c/720")))
    assert "https://c" not in repr(reading)


@pytest.mark.parametrize("item", [{"content_restricted": 1, "content_restricted_message": "Closed by the owner"},
                                  {"restriction": {"can_play": 0, "text": "Closed by the owner"}}])
def test_an_item_that_states_its_own_restriction_is_restricted(item):
    reading = read_item(item)

    assert (reading.kind, reading.message) == (Kind.RESTRICTED, "Closed by the owner")


@pytest.mark.parametrize("item", [{}, {"files": {}}, {"files": {"hls": "https://c/m.m3u8"}}, {"restriction": {"can_play": 1}}])
def test_an_item_without_progressive_files_and_without_a_restriction_is_not_sure(item):
    assert read_item(item).kind is Kind.NO_FILE


def test_a_single_call_reads_the_video_asked_for():
    called = read_call(json.dumps({"response": {"items": [{"owner_id": -10, "id": 20, "files": {"mp4_720": "https://c/1"}}]}}))

    assert read_single(called, VIDEO).kind is Kind.PLAYABLE
    assert read_single(called, ("-10", "21")).kind is Kind.NO_FILE


@pytest.mark.parametrize("error,kind,status", [({"error_code": 15, "error_msg": "Access denied"}, Kind.RESTRICTED, 200),
                                               ({"error_code": 204, "error_msg": "Access denied"}, Kind.RESTRICTED, 200),
                                               ({"error_code": 6, "error_msg": "Too many requests per second"}, Kind.HTTP_ERROR, 429),
                                               ({"error_code": 5, "error_msg": "User authorization failed"}, Kind.NO_ANSWER, 200),
                                               ({"error_code": 1, "error_msg": "Unknown"}, Kind.NO_ANSWER, 200)])
def test_an_error_of_a_single_call_is_a_refusal_only_when_it_says_the_video_is_refused(error, kind, status):
    reading = read_single(read_call(json.dumps({"error": error})), VIDEO)

    assert (reading.kind, reading.status) == (kind, status)


def test_a_single_call_that_is_no_answer_of_the_api_is_not_sure():
    assert read_single(None, VIDEO).kind is Kind.NOT_PLAYER


def test_the_files_of_the_older_player_are_read_from_its_params_with_the_escapes_decoded():
    assert page_files(OLD, VIDEO) == {720: "https://cdn.test/720.mp4?a=1&b=2", 2160: "https://cdn.test/2160.mp4"}


def test_the_prefetched_answer_of_the_page_gives_the_files_of_the_video_asked_for_and_no_other():
    assert page_files(PREFETCH, VIDEO) == {480: "https://cdn.test/480.mp4", 1080: "https://cdn.test/1080.mp4"}
    assert page_files(PREFETCH, ("-10", "21")) == {}


def test_bare_literals_of_a_page_are_the_last_place_the_files_are_looked_for():
    text = '<html><head><title>Video embed</title></head><script>x = {"mp4_360":"https:\\/\\/cdn.test\\/360.mp4"}</script></html>'

    assert page_files(text, VIDEO) == {360: "https://cdn.test/360.mp4"}


def test_a_page_that_lists_files_is_playable_and_carries_them():
    reading = read_page(200, OLD, VIDEO)

    assert (reading.kind, reading.resolutions) == (Kind.PLAYABLE, (720, 2160))
    assert dict(reading.files)[720] == "https://cdn.test/720.mp4?a=1&b=2"


def test_a_page_that_only_names_the_heights_is_playable_without_addresses():
    reading = read_page(200, "<html><title>Video embed</title><body>mp4_480 mp4_720</body></html>", VIDEO)

    assert (reading.kind, reading.resolutions, reading.files) == (Kind.PLAYABLE, (480, 720), ())


@pytest.mark.parametrize("words", ["Это видео скрыто настройками приватности и недоступно для просмотра", "Access denied", "This video is protected by privacy settings"])
def test_a_refusal_the_page_prints_outranks_the_files_it_lists(words):
    page = f"<html><head><title>Video embed</title></head><body><div id='video_ext_msg'>{words}</div><script>{OLD}</script></body></html>"

    assert read_page(200, page, VIDEO).kind is Kind.RESTRICTED


def test_the_words_of_a_script_are_not_a_refusal_the_page_prints():
    page = "<html><head><title>VK</title></head><body><script>var s = 'Access denied';</script></body></html>"

    assert read_page(200, page, VIDEO).kind is Kind.NOT_PLAYER


@pytest.mark.parametrize("status,kind", [(404, Kind.NOT_FOUND), (429, Kind.HTTP_ERROR), (503, Kind.HTTP_ERROR)])
def test_a_status_of_the_page_reads_as_its_kind(status, kind):
    assert read_page(status, "", VIDEO).kind is kind


@pytest.mark.parametrize("page,kind", [("<html><head><title>VK</title></head></html>", Kind.NOT_PLAYER),
                                       ("<html><head><title>Video embed</title></head><body></body></html>", Kind.NO_FILE),
                                       ("<html><head><title>Video embed</title></head><body>Видеофайл не найден</body></html>", Kind.NOT_FOUND)])
def test_a_page_without_files_and_without_a_refusal_is_a_shell_an_empty_player_or_a_missing_file(page, kind):
    assert read_page(200, page, VIDEO).kind is kind


FILES = ((144, "https://c/144"), (480, "https://c/480"), (720, "https://c/720"), (2160, "https://c/2160"))


@pytest.mark.parametrize("cap,height", [(None, 2160), (0, 2160), (720, 720), (1000, 720), (100, 144), (5000, 2160)])
def test_the_best_file_not_above_the_cap_is_taken(cap, height):
    assert pick_file(FILES, cap)[0] == height


def test_when_nothing_fits_under_the_cap_the_lowest_file_is_taken_and_no_files_give_none():
    assert pick_file(FILES[1:], 100)[0] == 480
    assert pick_file((), 720) is None
