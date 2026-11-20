"""The client of the socket of a provider (contracts/provider_socket.md): the five paths over HTTP, and nothing of what the
provider reads. A failure of the way or of the answer is a `ProviderError` with a coded reason; the provider's own refusals
(a card it cannot read, a file it cannot take) come as the fields of its answers, not as errors."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
from pydantic import BaseModel, ValidationError

from core.contracts import TitleSearchReply, TitleSearchRequest
from core.topics import SOURCES_TITLES_SEARCH, deadline_of
from core.contracts.provider import (PROVIDER_BAD_ANSWER, PROVIDER_REFUSED, PROVIDER_UNAVAILABLE, SILENCE_S, AdoptReply, AdoptRequest,
                                     DownloadFrame, DownloadRequest, Manifest, ReadFrame, ReadRequest)
from core.reasons import reason

# What a short request of the socket may take (a safeguard): the manifest, the connection of the feed. A silent stream is
# judged by SILENCE_S alone: the provider says it is alive every heartbeat.
REQUEST_TIMEOUT_S = 10.0
# How much shorter than the fuse of the bus request the wait for the provider is: the answer of a provider that is too slow
# is its reason, not the silence of a fuse that fired first.
FUSE_MARGIN_S = 5.0
# What the transfer of the old addresses may take (a safeguard): the provider reads a card of every one of them.
ADOPT_S = 600.0


class ProviderError(RuntimeError):
    """The socket failed: the provider does not answer, refuses, or answers against the specification. `transient` marks
    a cause that may pass (the provider is down), unlike a refusal or a broken answer."""

    def __init__(self, why: str, transient: bool = True) -> None:
        super().__init__(why)
        self.reason = why
        self.transient = transient


def _unavailable(exc: Exception) -> ProviderError:
    return ProviderError(reason(PROVIDER_UNAVAILABLE, cause=f"{type(exc).__name__}: {exc}"[:200]))


def _broken(what: str, exc: Exception | None = None) -> ProviderError:
    cause = f"{what}: {type(exc).__name__}: {exc}"[:200] if exc is not None else what
    return ProviderError(reason(PROVIDER_BAD_ANSWER, cause=cause), transient=False)


def _checked(response: httpx.Response) -> None:
    """The status of an answer: 4xx is a refusal of a request the tracker thought right, 5xx a failure of the provider."""
    code = response.status_code
    if code >= 500:
        raise ProviderError(reason(PROVIDER_UNAVAILABLE, status=code))
    if code >= 400:
        raise ProviderError(reason(PROVIDER_REFUSED, status=code), transient=False)


class ProviderClient:
    def __init__(self, url: str, token: str = "", *, search_s: float | None = None,
                 http: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        # The fuse of the request that waits for the sources of the search: that of the topic it answers (core/topics.py).
        self._search_s = search_s or (deadline_of(SOURCES_TITLES_SEARCH) or 0) - FUSE_MARGIN_S
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_S, read=SILENCE_S))

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _model(model: type[BaseModel], data: Any, what: str) -> Any:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise _broken(what, exc) from exc

    async def _post(self, path: str, body: BaseModel, seconds: float, model: type[BaseModel], what: str) -> Any:
        try:
            response = await self._http.post(f"{self.url}{path}", json=body.model_dump(mode="json"), headers=self._headers,
                                             timeout=seconds)
            _checked(response)
            data = response.json()
        except httpx.HTTPError as exc:
            raise _unavailable(exc) from exc
        except ValueError as exc:
            raise _broken(what, exc) from exc
        return self._model(model, data, what)

    async def manifest(self) -> Manifest:
        try:
            response = await self._http.get(f"{self.url}/v1/manifest", headers=self._headers, timeout=REQUEST_TIMEOUT_S)
            _checked(response)
            data = response.json()
        except httpx.HTTPError as exc:
            raise _unavailable(exc) from exc
        except ValueError as exc:
            raise _broken("manifest", exc) from exc
        return self._model(Manifest, data, "manifest")

    async def search(self, request: TitleSearchRequest) -> TitleSearchReply:
        return await self._post("/v1/search", request, self._search_s, TitleSearchReply, "search")

    async def adopt(self, request: AdoptRequest) -> AdoptReply:
        """The addresses of protocol 1 the provider turns into titles (transitional, appendix B): it reads each card, so it takes its time."""
        return await self._post("/v1/adopt", request, ADOPT_S, AdoptReply, "adopt")

    async def _frames(self, path: str, body: BaseModel, frame: type[BaseModel], what: str) -> AsyncIterator[Any]:
        """The frames of a request the provider answers line by line. Closing the generator (the work is stopped) closes the
        connection, which is how the provider is told to stop."""
        try:
            async with self._http.stream("POST", f"{self.url}{path}", json=body.model_dump(mode="json"),
                                         headers=self._headers, timeout=httpx.Timeout(REQUEST_TIMEOUT_S, read=SILENCE_S)) as response:
                _checked(response)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield frame.model_validate_json(line)
                    except ValidationError as exc:
                        raise _broken(what, exc) from exc
        except httpx.HTTPError as exc:
            raise _unavailable(exc) from exc

    def read(self, request: ReadRequest) -> AsyncIterator[ReadFrame]:
        return self._frames("/v1/read", request, ReadFrame, "read frame")

    def download(self, request: DownloadRequest) -> AsyncIterator[DownloadFrame]:
        return self._frames("/v1/download", request, DownloadFrame, "download frame")

    async def events(self) -> AsyncIterator[tuple[str, dict]]:
        """`(event, data)` of the feed, one for each event the provider sends; the comments of the heartbeat only keep the
        connection alive. The feed ends when the provider closes it; a silence of SILENCE_S is an error."""
        try:
            async with self._http.stream("GET", f"{self.url}/v1/events", headers=self._headers,
                                         timeout=httpx.Timeout(REQUEST_TIMEOUT_S, read=SILENCE_S)) as response:
                _checked(response)
                event, data = "", ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line.removeprefix("event:").strip()
                    elif line.startswith("data:"):
                        data += line.removeprefix("data:").strip()
                    elif not line.strip() and event:
                        try:
                            yield event, json.loads(data or "{}")
                        except ValueError as exc:
                            raise _broken("event", exc) from exc
                        event, data = "", ""
        except httpx.HTTPError as exc:
            raise _unavailable(exc) from exc
