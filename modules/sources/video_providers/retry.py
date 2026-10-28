"""Bounded retry of one request: a rate limit or a server hiccup must not look like absence."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx


def _pause(response: httpx.Response | None, fallback: float) -> float:
    try:
        return float(response.headers.get("Retry-After")) if response is not None else fallback
    except (TypeError, ValueError):
        return fallback


async def send_with_retry(send: Callable[[], Awaitable[httpx.Response]], attempts: int = 3,
                          base_delay: float = 1.0) -> httpx.Response:
    """Last response (or the last transport error) once `attempts` are spent."""
    response: httpx.Response | None = None
    error: httpx.HTTPError | None = None
    for attempt in range(attempts):
        try:
            response, error = await send(), None
        except httpx.HTTPError as exc:
            response, error = None, exc
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
        if attempt < attempts - 1:
            await asyncio.sleep(_pause(response, base_delay * (attempt + 1)))
    if response is None:
        assert error is not None
        raise error
    return response
