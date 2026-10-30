import inspect
import json
import re
from unittest.mock import AsyncMock

import httpx


def fake_response() -> AsyncMock:
    """A response the code under test can read: the header table is empty, as for an answer without headers."""
    response = AsyncMock(spec=httpx.Response)
    response.headers = {}
    return response


# What VK answers a direct call it refuses.
SHOW_DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
_REFUSALS = ("access denied", "access restricted", "protected by privacy", "private video")


def show_of(status: int, page: str) -> tuple[int, str]:
    """What the direct call of VK says of a video whose player page a test describes (the files a page lists, a refusal)."""
    if status != 200:
        return status, ""
    lowered = page.lower()
    if any(mark in lowered for mark in _REFUSALS):
        return 200, SHOW_DENIED
    heights = sorted({int(found) for found in re.findall(r"mp4_(\d+)", lowered)})
    if heights:
        params = {f"url{height}": f"https://cdn.test/{height}.mp4" for height in heights}
        return 200, json.dumps({"payload": [0, ["T", "<div/>", {"player": {"type": "vk", "params": [params]}}]]})
    return 200, page


def fake_client(shows: dict[str, str] | None = None) -> AsyncMock:
    """A client whose direct VK call answers as `shows` says (by video), else as the page fixtures of `client.get` say."""
    async def post(url, data=None, headers=None, **kwargs):
        shown = fake_response()
        shown.status_code = 200
        shown.text = SHOW_DENIED
        video = (data or {}).get("video", "")
        if video in (shows or {}):
            shown.text = shows[video]
            return shown
        page = client.get.side_effect
        if callable(page) and "_" in video:
            oid, number = video.rsplit("_", 1)
            answered = page(f"https://vkvideo.ru/video_ext.php?oid={oid}&id={number}&hash=abc")
            if inspect.isawaitable(answered):
                answered = await answered
            shown.status_code, shown.text = show_of(answered.status_code, answered.text)
        return shown

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = post
    return client
