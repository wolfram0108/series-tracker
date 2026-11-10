"""A ceiling on the body of every answer a client of the service reads whole.

A page, an API answer, a playlist or a key is text or bytes of a few megabytes at most, and every reader of one takes the
body whole into memory. A host may serve anything at an address: a parser of an application gives the address of an MP4
with no extension, a CDN ignores a Range, and the "playlist" is a video of hundreds of megabytes held in a process of a
machine of 4 GB. The transport of the client ends such an answer at the ceiling: the reader gets a transport error
(`AnswerTooLarge`, an `httpx.ReadError`), which every reader already takes for an answer that did not come. A download
of a file reads it in parts with a client of its own (modules/downloads/ranged.py), not through this transport.
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx

ANSWER_LIMIT_BYTES = 32 * 1024 * 1024


class AnswerTooLarge(httpx.ReadError):
    """The body of an answer is over the ceiling: it is no page, API answer or playlist."""


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int, request: httpx.Request) -> None:
        self._stream, self._limit, self._request = stream, limit, request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        read = 0
        async for chunk in self._stream:
            read += len(chunk)
            if read > self._limit:
                raise AnswerTooLarge(f"answer over {self._limit} bytes", request=self._request)
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class BoundedTransport(httpx.AsyncBaseTransport):
    """A transport whose answers end at `limit` bytes of body: at once when the length they announce is over it, else as
    soon as what was read is."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None, limit: int = ANSWER_LIMIT_BYTES) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._limit = limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        announced = response.headers.get("content-length", "")
        if announced.isdigit() and int(announced) > self._limit:
            await response.aclose()
            raise AnswerTooLarge(f"answer of {announced} bytes, over {self._limit}", request=request)
        response.stream = _BoundedStream(response.stream, self._limit, request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def bounded_client(*, limits: httpx.Limits | None = None, **options) -> httpx.AsyncClient:
    """A client of the service whose answers are read whole: every answer under the ceiling. `limits` are the
    connections it opens and keeps (httpx keeps 20 by default)."""
    inner = httpx.AsyncHTTPTransport(limits=limits) if limits is not None else None
    return httpx.AsyncClient(transport=BoundedTransport(inner), **options)
