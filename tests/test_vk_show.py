import json


from modules.sources.players.vk_show import best_stream


def answer(code, items) -> str:
    return json.dumps({"payload": [code, items], "static": "dist/x.js"}, ensure_ascii=False)


def with_player(**params) -> str:
    return answer(0, ["Title", "<div id='video_box_wrap-1_2'></div>", {"mvData": {}, "player": {"type": "vk", "params": [params]}}])


FULL = with_player(url144="https://cdn/144.mp4", url720="https://cdn/720.mp4", url2160="https://cdn/2160.mp4", hls="https://cdn/v.m3u8")
GONE = answer("8", ['"Видеозапись недоступна, так как была удалена."', "false", '""'])
DENIED = answer("8", ['"Ошибка доступа (1)"', "false", '""'])
OTHER = answer("8", ['"Что-то совсем новое"'])


def test_the_best_stream_is_the_highest_file_and_the_manifest_only_when_there_is_none():
    assert best_stream(FULL) == "https://cdn/2160.mp4"
    assert best_stream(with_player(hls="https://cdn/v.m3u8")) == "https://cdn/v.m3u8"
    assert best_stream(GONE) is None and best_stream("nonsense") is None
