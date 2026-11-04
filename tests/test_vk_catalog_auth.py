"""The player token expires and may be echoed by VK on rejection."""
import json
from urllib.parse import parse_qs

import pytest
import requests

from modules.trackerauth.vk_catalog import VkCatalogClient


@pytest.mark.parametrize("milliseconds", [False, True])
def test_catalog_renews_expiring_token(monkeypatch, milliseconds):
    now = [1_790_000_000.0]
    monkeypatch.setattr("modules.trackerauth.vk_catalog.time.time", lambda: now[0])
    issued, sent = [], []

    def send(adapter, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        if request.method == "GET":
            issued.append(f"token-{len(issued)}")
            expiry = (now[0] + 120) * (1000 if milliseconds else 1)
            content = 'webToken: ' + json.dumps({"access_token": issued[-1], "expired_at": expiry})
        else:
            sent.append(parse_qs(request.body)["access_token"][0])
            content = '{"response": {}}'
        response._content = content.encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with requests.Session() as session:
        client = VkCatalogClient()
        for elapsed in [0, 10, 61]:
            now[0] = 1_790_000_000.0 + elapsed
            client.fetch(session, "catalog.getOwnerVideosSearch", {"owner_id": -42, "q": "x"}, 20)
    assert issued == ["token-0", "token-1"]
    assert sent == ["token-0", "token-0", "token-1"]


def test_catalog_repeated_token_rejection_is_bounded_and_redacted(monkeypatch):
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(request.method)
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        if request.method == "GET":
            content = 'webToken: {"access_token":"private-web-token"}'
        else:
            content = json.dumps({"error": {"error_code": 5, "error_msg": "User authorization failed",
                                          "request_params": [{"key": "access_token", "value": "private-web-token"}]}})
        response._content = content.encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with requests.Session() as session:
        response = VkCatalogClient().fetch(session, "catalog.getOwnerVideosSearch", {}, 20)
    assert calls == ["GET", "POST", "GET", "POST"]
    assert response.json()["error"]["error_code"] == 5
    assert "private-web-token" not in response.text


def test_catalog_paces_searches_sharing_session_without_extra_pause_after_slow_response(monkeypatch):
    now = [100.0]
    waits, starts = [], []
    durations = iter([0.2, 0.8, 0.1])
    monkeypatch.setattr("modules.trackerauth.vk_catalog.time.monotonic", lambda: now[0])

    def sleep(delay):
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("modules.trackerauth.vk_catalog.time.sleep", sleep)

    def send(adapter, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        if request.method == "GET":
            content = 'webToken: {"access_token":"web-token"}'
        else:
            starts.append(now[0])
            now[0] += next(durations)
            content = '{"response": {}}'
        response._content = content.encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with requests.Session() as session:
        client = VkCatalogClient()
        for method in ["catalog.getOwnerVideosSearch", "catalog.getBlockItems", "catalog.getOwnerVideosSearch"]:
            client.fetch(session, method, {}, 20)
    assert starts == pytest.approx([100.0, 100.5, 101.3])
    assert waits == pytest.approx([0.3])
