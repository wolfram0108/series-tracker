

from modules.sources.players.vk_files import page_files

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


def test_the_prefetched_answer_of_the_page_gives_the_files_of_the_video_asked_for_and_no_other():
    assert page_files(PREFETCH, VIDEO) == {480: "https://cdn.test/480.mp4", 1080: "https://cdn.test/1080.mp4"}
    assert page_files(PREFETCH, ("-10", "21")) == {}


FILES = ((144, "https://c/144"), (480, "https://c/480"), (720, "https://c/720"), (2160, "https://c/2160"))
