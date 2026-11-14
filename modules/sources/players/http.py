"""One request to a player host, reported as an `Answer`; asking again is the business of `policy`, not of this."""
from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit

import httpx

from .contract import Answer, Kind, Reading

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_TIMEOUT_S = 8.0


def failure_words(exc: BaseException, address: str) -> str:
    """What a transport failure says, with the cause beneath it: `ConnectError` alone does not tell a host that refuses the
    connection from a handshake that fails or a name that does not resolve, and the cure of each is different."""
    root = exc
    while (root.__cause__ or root.__context__) is not None:
        root = root.__cause__ or root.__context__
    cause = f" ({type(root).__name__}: {str(root)[:80]})" if root is not exc else ""
    return f"{type(exc).__name__}: {urlsplit(address).hostname}{cause}"


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def get_answer(http: httpx.AsyncClient, url: str, referer: str, agent: str = USER_AGENT, *,
                     headers: dict | None = None, params: dict | None = None,
                     timeout: "float | httpx.Timeout" = REQUEST_TIMEOUT_S) -> Answer:
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
