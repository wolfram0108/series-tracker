"""Registry for web video providers."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

    from .base import BaseVideoProvider


class VideoProviderRegistry:
    def __init__(self) -> None:
        self._providers: list[BaseVideoProvider] = []

    def register(self, provider: BaseVideoProvider) -> None:
        self._providers.append(provider)

    def find_provider(self, url: str) -> BaseVideoProvider | None:
        """Find matching provider by release URL."""
        for p in self._providers:
            if p.match_url(url):
                return p
        return None

    def get_provider(self, name: str) -> BaseVideoProvider | None:
        for p in self._providers:
            if p.name == name:
                return p
        return None

    async def resolve_stream(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Query registered providers to resolve a direct stream descriptor."""
        for p in self._providers:
            stream_info = await p.resolve_stream(url, http)
            if stream_info:
                return stream_info
        return None
