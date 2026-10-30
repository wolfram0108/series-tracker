import pytest

from modules.sources.video_providers.vk_embed import EmbedKind, read_embed

# The shapes below are the ones VK was seen to answer: the player page, the same page without the file,
# and the site shell that comes instead of the player.
PLAYER = '<html lang="ru"><head><title>Video embed</title></head><body><script>{"mp4_720":"a","mp4_1080":"b","mp4_720":"c"}</script></body></html>'
PLAYER_4K = '<html><head><title>Video embed</title></head><body><script>{"mp4_1080":"a","mp4_2160":"b"}</script></body></html>'
NO_FILE = ("<html><head><title>Video embed</title></head><body><div>Видеофайл не найден.</div>"
           "<div>Открыть ВКонтакте</div></body></html>")
PRIVATE = '<html><head><title>Video embed</title></head><body><script>{"mp4_720":"a"}</script><div>Access denied</div></body></html>'
EMPTY = "<html><head><title>Video embed</title><style>a{}</style></head><body><p>Что-то  пошло\n не так</p><script>x()</script></body></html>"
SHELL = '<!DOCTYPE html><html lang="EN"><head><meta charset="windows-1251"/><title>VK</title></head><body><div id="app"></div></body></html>'


def test_a_player_page_with_files_is_playable_with_its_resolutions():
    answer = read_embed(200, PLAYER)

    assert (answer.kind, answer.resolutions) == (EmbedKind.PLAYABLE, (720, 1080))


def test_resolutions_are_numbers_not_text():
    assert read_embed(200, PLAYER_4K).resolutions == (1080, 2160)


def test_a_refusal_outranks_the_files_the_page_still_lists():
    answer = read_embed(200, PRIVATE)

    assert (answer.kind, answer.message) == (EmbedKind.RESTRICTED, "access denied")


def test_the_player_page_that_says_the_file_is_not_found_is_not_found():
    answer = read_embed(200, NO_FILE)

    assert answer.kind == EmbedKind.NOT_FOUND


def test_status_404_is_not_found():
    assert read_embed(404, "").kind == EmbedKind.NOT_FOUND


@pytest.mark.parametrize("status", [403, 408, 425, 429, 500, 503, 302])
def test_any_other_status_is_an_http_error_that_keeps_the_status(status):
    answer = read_embed(status, PLAYER)

    assert (answer.kind, answer.status) == (EmbedKind.HTTP_ERROR, status)


def test_a_player_page_without_files_and_without_a_known_message_says_what_it_shows():
    answer = read_embed(200, EMPTY)

    assert (answer.kind, answer.title, answer.message) == (EmbedKind.EMPTY_EMBED, "Video embed", "Что-то пошло не так")


def test_the_site_shell_is_not_the_player_and_names_its_title():
    answer = read_embed(200, SHELL)

    assert (answer.kind, answer.title, answer.resolutions) == (EmbedKind.NOT_EMBED, "VK", ())


@pytest.mark.parametrize("text", ["", "ok", "<html></html>", "<title></title>", "{\"error\": 1}"])
def test_a_page_that_is_nothing_known_is_not_the_player(text):
    assert read_embed(200, text).kind == EmbedKind.NOT_EMBED


@pytest.mark.parametrize("status,text", [(200, PLAYER), (200, NO_FILE), (200, PRIVATE), (200, EMPTY), (200, SHELL),
                                         (200, ""), (404, ""), (429, ""), (500, SHELL)])
def test_every_answer_is_exactly_one_kind_and_reads_the_same_each_time(status, text):
    first, second = read_embed(status, text), read_embed(status, text)

    assert first == second and isinstance(first.kind, EmbedKind)
