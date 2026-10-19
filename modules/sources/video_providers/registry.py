"""Реестр провайдеров веб-видео."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseVideoProvider


class VideoProviderRegistry:
    def __init__(self) -> None:
        self._providers: list[BaseVideoProvider] = []

    def register(self, provider: BaseVideoProvider) -> None:
        self._providers.append(provider)

    def find_provider(self, url: str) -> BaseVideoProvider | None:
        """Находит подходящий провайдер по URL."""
        for p in self._providers:
            if p.match_url(url):
                return p
        return None

    def get_provider(self, name: str) -> BaseVideoProvider | None:
        for p in self._providers:
            if p.name == name:
                return p
        return None
