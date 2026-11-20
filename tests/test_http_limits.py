"""The ceiling on the body of an answer (core/http_limits.py): an answer over it ends at once when its length says so, or
as soon as what was read is over it, as a transport error every reader of an answer already takes; the clients of the
service that read answers whole are made with it."""
import httpx
import pytest

from core.http_limits import AnswerTooLarge, BoundedTransport
from modules.sources.players.http import get_answer

LIMIT = 1024 * 1024


def served(read: list[int]):
    """A host whose `/announced` is a body of 8 MB with its length, `/chunked` the same without one, `/small` a playlist."""
    async def chunks():
        for _ in range(64):
            read.append(128 * 1024)
            yield b"\0" * (128 * 1024)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/announced":
            return httpx.Response(200, headers={"Content-Length": str(8 * LIMIT)}, content=b"\0" * (8 * LIMIT))
        if request.url.path == "/chunked":
            return httpx.Response(200, content=chunks())
        return httpx.Response(200, text="#EXTM3U\n")
    return handle


async def test_an_answer_over_the_ceiling_ends_as_an_answer_that_did_not_come_and_one_under_it_is_whole():
    read: list[int] = []
    async with httpx.AsyncClient(transport=BoundedTransport(httpx.MockTransport(served(read)), LIMIT)) as http:
        assert (await get_answer(http, "https://host/announced", "")).error == "AnswerTooLarge"
        with pytest.raises(AnswerTooLarge):
            await http.get("https://host/chunked")
        # The read stopped at the ceiling: the rest of the body was never taken from the host.
        assert sum(read) <= LIMIT + 128 * 1024
        assert (await get_answer(http, "https://host/small", "")).text == "#EXTM3U\n"
