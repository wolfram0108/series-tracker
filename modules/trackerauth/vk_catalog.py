"""VK Video's channel-search API uses a short-lived web token, not the saved user token.

The public player shell issues this token itself. Keep it inside trackerauth and
send it in a POST body, so neither the bus nor request URLs carry it.
"""
from __future__ import annotations

import json
import re
import time
from weakref import WeakKeyDictionary

import requests

from .providers import TrackerLoginError

METHODS = frozenset({"catalog.getOwnerVideosSearch", "catalog.getBlockItems"})
API_BASE = "https://api.vkvideo.ru/method/"
API_VERSION = "5.289"
REQUEST_INTERVAL = 0.5  # At most two catalog requests/second per session.
PLAYER_URL = "https://vkvideo.ru/video_ext.php"
HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://vkvideo.ru", "Referer": PLAYER_URL}


class VkCatalogClient:
    def __init__(self) -> None:
        self._tokens: WeakKeyDictionary[requests.Session, tuple[str, float]] = WeakKeyDictionary()
        self._last_calls: WeakKeyDictionary[requests.Session, float] = WeakKeyDictionary()

    def _token(self, session: requests.Session, timeout: float) -> str:
        cached = self._tokens.get(session)
        if cached and cached[1] > time.time():
            return cached[0]
        response = session.get(PLAYER_URL, headers=HEADERS, timeout=timeout)
        if response.status_code != 200:
            raise TrackerLoginError(f"VK: получение ключа поиска: HTTP {response.status_code}")
        for match in re.finditer(r'(?:\bwebToken|"webToken")\s*:\s*', response.text):
            try:
                value = json.JSONDecoder().raw_decode(response.text[match.end():].lstrip())[0]
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            token = value.get("access_token")
            if not isinstance(token, str) or not token:
                continue
            expiry = value.get("expired_at")
            if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
                expiry = time.time() + 300
            elif expiry > 100_000_000_000:
                expiry /= 1000
            self._tokens[session] = (token, expiry - 60)
            return token
        raise TrackerLoginError("VK: плеер не выдал ключ быстрого поиска")

    def fetch(self, session: requests.Session, method: str, params: dict,
              timeout: float) -> requests.Response:
        if method not in METHODS:
            raise TrackerLoginError("VK: неизвестный метод поиска")
        for attempt in range(2):
            token = self._token(session, timeout)
            # Trackerauth serializes calls to VK. Count time spent on the
            # previous HTTP request toward the interval instead of adding a
            # fixed pause after every 20-video page.
            last = self._last_calls.get(session)
            if last is not None:
                delay = REQUEST_INTERVAL - (time.monotonic() - last)
                if delay > 0:
                    time.sleep(delay)
            self._last_calls[session] = time.monotonic()
            response = session.post(
                API_BASE + method,
                data={**params, "v": API_VERSION, "access_token": token},
                headers=HEADERS, timeout=timeout)
            try:
                body = response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
            except ValueError:
                return response  # Delivery reports the malformed response.
            if isinstance(error, dict) and "request_params" in error:
                # VK echoes the submitted token in error.request_params.
                error.pop("request_params")
                response._content = json.dumps(body, ensure_ascii=False).encode("utf-8")
                response.encoding = "utf-8"
            if not isinstance(error, dict) or error.get("error_code") != 5 or attempt:
                return response
            self._tokens.pop(session, None)
        return response
