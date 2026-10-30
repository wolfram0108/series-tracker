from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from modules.sources.players.contract import Answer, Kind, Reading
from modules.sources.players.http import get_answer, read_answer


def client_answering(response=None, error=None):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = error if error else None
    client.get.return_value = response
    return client


@pytest.mark.asyncio
async def test_a_response_becomes_an_answer_with_the_wait_the_host_asks_for():
    response = MagicMock(status_code=429, text="slow down", headers={"Retry-After": "7"})

    answer = await get_answer(client_answering(response), "https://p.test/1", "https://page.test")

    assert answer == Answer(429, "slow down", 7.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}])
async def test_a_wait_that_is_not_a_number_is_no_wait(headers):
    response = MagicMock(status_code=503, text="", headers=headers)

    answer = await get_answer(client_answering(response), "https://p.test/1", "https://page.test")

    assert answer.retry_after is None


@pytest.mark.asyncio
async def test_a_transport_failure_is_an_answer_without_a_status_not_an_exception():
    answer = await get_answer(client_answering(error=httpx.ReadTimeout("slow")), "https://p.test/1", "https://page.test")

    assert (answer.status, answer.error) == (None, "ReadTimeout")


@pytest.mark.asyncio
async def test_one_request_carries_the_page_as_referer():
    client = client_answering(MagicMock(status_code=200, text="ok", headers={}))

    await get_answer(client, "https://p.test/1", "https://page.test/ep")

    assert client.get.await_count == 1
    assert client.get.await_args.kwargs["headers"]["Referer"] == "https://page.test/ep"


def test_no_status_reads_as_no_answer_and_anything_else_goes_to_the_reader():
    assert read_answer(Answer(None, error="ReadTimeout"), lambda s, t: Reading(Kind.PLAYABLE)).kind == Kind.NO_ANSWER
    assert read_answer(Answer(200, "x"), lambda s, t: Reading(Kind.NOT_FOUND, s)).kind == Kind.NOT_FOUND
