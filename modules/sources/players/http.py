"""One request to a player host, reported as an `Answer`; asking again is the business of `policy`, not of this."""
from __future__ import annotations

from typing import Callable

import httpx

from .contract import Answer, Kind, Reading

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_TIMEOUT_S = 8.0


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def get_answer(http: httpx.AsyncClient, url: str, referer: str, agent: str = USER_AGENT, *,
                     headers: dict | None = None, params: dict | None = None, timeout: float = REQUEST_TIMEOUT_S) -> Answer:
    """`headers` replace the default pair when a host wants its own (an Origin, a fixed agent)."""
    try:
        response = await http.get(url, headers=headers or {"Referer": referer, "User-Agent": agent}, params=params,
                                  follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        return Answer(None, error=type(exc).__name__)
    return Answer(response.status_code, response.text, retry_after(response), final_url=str(response.url))


async def post_answer(http: httpx.AsyncClient, url: str, data: dict, headers: dict, *,
                      timeout: float = REQUEST_TIMEOUT_S) -> Answer:
    try:
        response = await http.post(url, data=data, headers={"User-Agent": USER_AGENT, **headers},
                                   follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        return Answer(None, error=type(exc).__name__)
    return Answer(response.status_code, response.text, retry_after(response))


def read_answer(answer: Answer, reader: Callable[[int, str], Reading]) -> Reading:
    """A transport failure reads as no answer; anything else goes to the player's own reader."""
    if answer.status is None:
        return Reading(Kind.NO_ANSWER, message=answer.error)
    return reader(answer.status, answer.text)
