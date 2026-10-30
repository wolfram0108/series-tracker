import pytest

from modules.sources.players.allvideo import best_stream_url, read_playerjs
from modules.sources.players.contract import Kind

# The shapes below are the ones AllVideo was seen to answer: the player with its file list, and the placeholder
# that comes with status 404 for an embed that does not exist.
PLAYER = ('<html><head><title>Серия 1 4K</title></head><body><script>new Playerjs({ id: "v", '
          'file:"[360p]https://h/360.mp4/,[720p]https://h/720.mp4/,[1080p]https://h/1080.mp4/" });</script></body></html>')
PLAYER_4K = '<script>new Playerjs({ file:"[720p]u,[1080p]u,[4k]u" })</script>'
BY_NAME = '<script>new Playerjs({ file:"https://video.host/stream_720p.mp4" })</script>'
NO_FILE = "<html><head><title>Серия 2</title></head><body><script>new Playerjs({ id: 'v' })</script></body></html>"
PLACEHOLDER = "<html><head><title>404</title></head><script>new Playerjs({ file: '/contents/other/video_error.mp4' })</script></html>"
TITLE_ONLY = "<html><head><title>Watch video 1080p HD</title></head><body>hello</body></html>"


def test_the_file_list_gives_its_resolutions_and_the_title_does_not():
    answer = read_playerjs(200, PLAYER)

    assert (answer.kind, answer.resolutions) == (Kind.PLAYABLE, (360, 720, 1080))


def test_a_4k_tag_is_2160():
    assert read_playerjs(200, PLAYER_4K).resolutions == (720, 1080, 2160)


def test_a_resolution_in_the_name_of_the_file_counts():
    assert read_playerjs(200, BY_NAME).resolutions == (720,)


def test_a_player_without_a_file_list_says_so_and_names_its_title():
    answer = read_playerjs(200, NO_FILE)

    assert (answer.kind, answer.title, answer.resolutions) == (Kind.NO_FILE, "Серия 2", ())


def test_a_page_that_is_no_player_is_not_read_by_its_title():
    answer = read_playerjs(200, TITLE_ONLY)

    assert (answer.kind, answer.resolutions) == (Kind.NOT_PLAYER, ())


@pytest.mark.parametrize("status", [200, 404])
def test_the_placeholder_file_is_a_video_that_is_not_found(status):
    assert read_playerjs(status, PLACEHOLDER).kind == Kind.NOT_FOUND


@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_any_other_status_is_an_http_error_that_keeps_the_status(status):
    answer = read_playerjs(status, PLAYER)

    assert (answer.kind, answer.status) == (Kind.HTTP_ERROR, status)


@pytest.mark.parametrize("status,text", [(200, PLAYER), (200, NO_FILE), (200, PLACEHOLDER), (200, ""), (404, ""), (500, "x")])
def test_every_answer_is_exactly_one_kind_and_reads_the_same_each_time(status, text):
    first, second = read_playerjs(status, text), read_playerjs(status, text)

    assert first == second and isinstance(first.kind, Kind)


def test_the_best_stream_is_the_highest_tagged_one():
    assert best_stream_url("[360p]https://h/a.mp4,[1080p]https://h/b.mp4,[720p]https://h/c.mp4") == "https://h/b.mp4"
    assert best_stream_url("[720p]u,[4k]https://h/4k.mp4") == "https://h/4k.mp4"
