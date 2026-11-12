"""The qBittorrent client: what is asked again, what is final, and how a stale session is renewed."""
import asyncio

import httpx
import pytest

from modules.torrents.qbt_client import QbtClient, QbtError

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
async def test_a_good_answer_is_taken_at_once():
    qbt, seen = client_with(lambda request, n: httpx.Response(200, text="v5"))
    assert (await qbt.version()) == "v5" and len(seen) == 1


@pytest.mark.asyncio
async def test_a_dropped_connection_is_asked_again_until_the_attempts_are_spent():
    def handler(request, n):
        if n < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, text="v5")

    qbt, seen = client_with(handler)
    assert (await qbt.version()) == "v5" and len(seen) == 3


@pytest.mark.asyncio
async def test_a_host_that_never_answers_ends_in_a_qbt_error_naming_the_attempts():
    def handler(request, n):
        raise httpx.ReadTimeout("slow")

    qbt, seen = client_with(handler)
    with pytest.raises(QbtError, match="3"):
        await qbt.version()
    assert len(seen) == 3


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


@pytest.mark.asyncio
async def test_a_missing_resource_is_none_and_is_not_asked_again():
    qbt, seen = client_with(lambda request, n: httpx.Response(404))
    assert await qbt._request("GET", "/api/v2/torrents/files") is None and len(seen) == 1


@pytest.mark.asyncio
async def test_a_conflict_is_an_answer_only_where_the_caller_expects_it():
    qbt, seen = client_with(lambda request, n: httpx.Response(409))
    assert (await qbt._request("POST", "/api/v2/torrents/add", conflict_ok=True)).status_code == 409
    with pytest.raises(httpx.HTTPStatusError):
        await qbt._request("POST", "/api/v2/torrents/add")


@pytest.mark.asyncio
async def test_a_server_error_is_raised_and_not_asked_again():
    qbt, seen = client_with(lambda request, n: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await qbt._request("GET", "/api/v2/torrents/info")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_the_infohash_qbittorrent_keys_the_torrent_by_is_found_after_it_appears():
    def handler(request, n):
        return httpx.Response(200, json=[{"hash": "bbb"}] if n >= 2 else [])

    qbt, seen = client_with(handler)
    assert await qbt.find_hash(["aaa", "bbb"], attempts=4, delay=0.0) == "bbb" and len(seen) == 2


@pytest.mark.asyncio
async def test_an_address_without_a_protocol_is_refused_at_once_not_asked_again():
    qbt = QbtClient("qbit.example", "user", "secret", retries=4, retry_delay=30.0)  # a retry would wait half a minute
    try:
        with pytest.raises(QbtError, match='"code":"qbt_retries_exhausted".*"attempts":1,'):
            await asyncio.wait_for(qbt._request("GET", "/api/v2/torrents/info"), 5)
    finally:
        await qbt.close()
