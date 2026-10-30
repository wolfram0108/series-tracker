import json

import pytest

from modules.sources.players.contract import Kind
from modules.sources.players.vk_show import best_stream, read_show


def answer(code, items) -> str:
    return json.dumps({"payload": [code, items], "static": "dist/x.js"}, ensure_ascii=False)


def with_player(**params) -> str:
    return answer(0, ["Title", "<div id='video_box_wrap-1_2'></div>", {"mvData": {}, "player": {"type": "vk", "params": [params]}}])


FULL = with_player(url144="https://cdn/144.mp4", url720="https://cdn/720.mp4", url2160="https://cdn/2160.mp4", hls="https://cdn/v.m3u8")
GONE = answer("8", ['"Видеозапись недоступна, так как была удалена."', "false", '""'])
DENIED = answer("8", ['"Ошибка доступа (1)"', "false", '""'])
BLOCKED = answer("8", ['"Видеозапись недоступна, так как её автор был заблокирован."'])
LIMITED = answer("8", ["<!>Доступ ограничен"])  # the words VK was seen to say of the videos that only the player page opens
LATER = answer("8", ["<!>Видео временно недоступно"])
OTHER = answer("8", ['"Что-то совсем новое"'])


def test_the_player_parameters_give_the_resolutions_in_order():
    answer_ = read_show(200, FULL)

    assert (answer_.kind, answer_.resolutions) == (Kind.PLAYABLE, (144, 720, 2160))


def test_a_manifest_alone_is_a_video_without_a_stated_resolution():
    answer_ = read_show(200, with_player(hls="https://cdn/v.m3u8"))

    assert (answer_.kind, answer_.resolutions) == (Kind.PLAYABLE, ())


def test_raw_line_breaks_inside_strings_do_not_break_the_reading():
    text = FULL.replace('"Title"', '"Ti\ntle\twith breaks"')

    assert read_show(200, text).kind == Kind.PLAYABLE


@pytest.mark.parametrize("text,kind", [(GONE, Kind.NOT_FOUND), (DENIED, Kind.RESTRICTED), (BLOCKED, Kind.RESTRICTED), (LIMITED, Kind.RESTRICTED),
                                       (LATER, Kind.NO_ANSWER), (OTHER, Kind.NOT_PLAYER)])
def test_what_vk_says_in_words_names_the_kind(text, kind):
    assert read_show(200, text).kind == kind


def test_a_login_code_is_a_refusal():
    assert read_show(200, answer("3", [])).kind == Kind.RESTRICTED


@pytest.mark.parametrize("text", [answer(0, ["Title", "<div/>", {"player": {"params": [{}]}}]), answer(0, ["Title"]),
                                  answer(0, ["Title", {"player": None}])])
def test_a_success_without_streams_is_a_player_without_a_file(text):
    assert read_show(200, text).kind == Kind.NO_FILE


@pytest.mark.parametrize("text", ["", "<html>shell</html>", "{}", '{"payload": "x"}', '{"payload": [0]}'])
def test_anything_that_is_not_the_envelope_is_not_a_player_answer(text):
    assert read_show(200, text).kind == Kind.NOT_PLAYER


def test_statuses_are_read_as_they_are():
    assert read_show(404, "").kind == Kind.NOT_FOUND
    assert (read_show(429, FULL).kind, read_show(429, FULL).status) == (Kind.HTTP_ERROR, 429)


@pytest.mark.parametrize("status,text", [(200, FULL), (200, GONE), (200, DENIED), (200, LATER), (200, OTHER), (200, ""), (404, ""), (500, FULL)])
def test_every_answer_is_exactly_one_kind_and_reads_the_same_each_time(status, text):
    first, second = read_show(status, text), read_show(status, text)

    assert first == second and isinstance(first.kind, Kind)


def test_the_best_stream_is_the_highest_file_and_the_manifest_only_when_there_is_none():
    assert best_stream(FULL) == "https://cdn/2160.mp4"
    assert best_stream(with_player(hls="https://cdn/v.m3u8")) == "https://cdn/v.m3u8"
    assert best_stream(GONE) is None and best_stream("nonsense") is None
