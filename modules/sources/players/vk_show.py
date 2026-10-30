"""Pure reading of the answer of VK's own video call, the one behind a direct link vk.com/video<oid>_<id>.

The call (al_video.php, act=show) answers the same whichever of the two player pages VK serves for an embed,
so a reading of it does not depend on that choice. Nothing here touches the network or the clock.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .contract import Kind, Reading

ENDPOINT = "https://vk.com/al_video.php"
MESSAGE_LENGTH = 200
# How VK says in words why there is no video; the first group of marks that fits names the kind.
NOT_FOUND_MARKS = ("была удалена", "не существует", "unknown error", "does not exist", "has been deleted")
RESTRICTED_MARKS = ("ошибка доступа", "доступ ограничен", "access denied", "access restricted", "please log in", "зарегистрированных", "заблокирован",
                    "правообладател", "rightholder", "copyright", "приватн", "private")
LATER_MARKS = ("временно недоступно", "temporarily unavailable")
LOGIN_CODE = "3"

_HEIGHT = re.compile(r"url(\d+)")


def _envelope(text: str) -> tuple[Any, list] | None:
    try:
        data = json.loads(text, strict=False)
    except ValueError:
        return None
    payload = data.get("payload") if isinstance(data, dict) else None
    if isinstance(payload, list) and len(payload) == 2 and isinstance(payload[1], list):
        return payload[0], payload[1]
    return None


def _params(items: list) -> dict | None:
    for item in reversed(items):
        player = item.get("player") if isinstance(item, dict) else None
        params = player.get("params") if isinstance(player, dict) else None
        if isinstance(params, list) and params and isinstance(params[0], dict):
            return params[0]
    return None


def _words(items: list) -> str:
    first = items[0] if items and isinstance(items[0], str) else ""
    return first.replace("<!>", " ").strip().strip('"').strip()


def read_show(status: int, text: str) -> Reading:
    if status == 404:
        return Reading(Kind.NOT_FOUND, status)
    if status != 200:
        return Reading(Kind.HTTP_ERROR, status)
    parsed = _envelope(text)
    if parsed is None:
        return Reading(Kind.NOT_PLAYER, status, message=" ".join(text.split())[:MESSAGE_LENGTH])
    code, items = parsed
    if str(code) == "0":
        params = _params(items)
        if params is None:
            return Reading(Kind.NO_FILE, status, message="no player parameters")
        files = tuple(sorted((int(found.group(1)), value) for key, value in params.items()
                             if (found := _HEIGHT.fullmatch(key)) and isinstance(value, str) and value.startswith(("http://", "https://"))))
        heights = tuple(sorted({int(found.group(1)) for key in params if (found := _HEIGHT.fullmatch(key))}))
        if heights or params.get("hls") or any(key.startswith("dash") for key in params):
            return Reading(Kind.PLAYABLE, status, heights, files=files)
        return Reading(Kind.NO_FILE, status, message="no stream keys")
    if str(code) == LOGIN_CODE:
        return Reading(Kind.RESTRICTED, status, message="login required")
    words = _words(items)
    lowered = words.lower()
    for marks, kind in ((NOT_FOUND_MARKS, Kind.NOT_FOUND), (RESTRICTED_MARKS, Kind.RESTRICTED), (LATER_MARKS, Kind.NO_ANSWER)):
        if any(mark in lowered for mark in marks):
            return Reading(kind, status, message=words[:MESSAGE_LENGTH])
    return Reading(Kind.NOT_PLAYER, status, message=words[:MESSAGE_LENGTH])


def best_stream(text: str) -> str | None:
    """The address of the highest progressive file the answer lists, else its HLS manifest."""
    parsed = _envelope(text)
    params = _params(parsed[1]) if parsed and str(parsed[0]) == "0" else None
    if not params:
        return None
    files = {int(found.group(1)): value for key, value in params.items()
             if (found := _HEIGHT.fullmatch(key)) and isinstance(value, str) and value}
    if files:
        return files[max(files)]
    return params.get("hls") if isinstance(params.get("hls"), str) and params["hls"] else None
