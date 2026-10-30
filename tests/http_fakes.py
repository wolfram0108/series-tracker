from unittest.mock import AsyncMock

import httpx


def fake_response() -> AsyncMock:
    """A response the code under test can read: the header table is empty, as for an answer without headers."""
    response = AsyncMock(spec=httpx.Response)
    response.headers = {}
    return response
