"""The qBittorrent client: what is asked again, what is final, and how a stale session is renewed."""

import httpx
import pytest

from modules.torrents.qbt_client import QbtClient

BASE = "http://qbt.test"


def client_with(handler) -> tuple[QbtClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    qbt = QbtClient(BASE, "user", "secret", retries=3, retry_delay=0.0)
    qbt._http = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(recording))
    return qbt, seen


@pytest.mark.asyncio
async def test_a_stale_session_is_renewed_once_and_the_operation_repeated():
    def handler(request, n):
        path = request.url.path
        if path == "/api/v2/app/version":
            return httpx.Response(403) if not any(r.url.path == "/api/v2/auth/login" for r in seen) else httpx.Response(200, text="v5")
        if path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        return httpx.Response(200, text="5.0")

    qbt, seen = client_with(handler)
    assert (await qbt.version()) == "v5"
    assert [r.url.path for r in seen] == ["/api/v2/app/version", "/api/v2/auth/login", "/api/v2/app/webapiVersion",
                                          "/api/v2/app/version"]
