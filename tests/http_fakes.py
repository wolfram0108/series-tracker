from unittest.mock import AsyncMock

import httpx


def fake_response() -> AsyncMock:
    """A response the code under test can read: the header table is empty, as for an answer without headers."""
    response = AsyncMock(spec=httpx.Response)
    response.headers = {}
    return response


# What VK answers a direct call it refuses: tests that read the player page of a video go through this by default.
SHOW_DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'


def fake_client(shows: dict[str, str] | None = None) -> AsyncMock:
    """A client whose direct VK call refuses (or answers as `shows` says, by the video it is asked for)."""
    async def post(url, data=None, headers=None, **kwargs):
        shown = fake_response()
        shown.status_code = 200
        shown.text = SHOW_DENIED
        for video, body in (shows or {}).items():
            if video in str(data):
                shown.text = body
        return shown

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = post
    return client
