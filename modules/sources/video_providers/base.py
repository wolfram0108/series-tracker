"""Базовый контракт провайдера видео-источников (веб-сайтов онлайн-просмотра)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


class BaseVideoProvider(ABC):
    """Интерфейс для провайдеров веб-видео (Ruchime, Kodik, AnimeLib и др.)."""

    name: str
    domains: tuple[str, ...]

    @abstractmethod
    def match_url(self, url: str) -> bool:
        """Проверяет, обслуживает ли провайдер данный URL."""
        ...

    @abstractmethod
    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Первичный разбор ссылки релиза:
        Возвращает:
        {
            "canonical_url": str,
            "title": {"ru": str | None, "en": str | None},
            "titles": list[str],
            "episodes_count": int,
            "provider": str,
            "source_type": "web_video",
        }
        """
        ...

    @abstractmethod
    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict) -> list[dict]:
        """Сканирование эпизодов для формирования списка видео-фактов:
        Возвращает:
        [
            {
                "title": str,
                "url": str,
                "publication_date": str,  # ISO в формате UTC (Z)
                "resolution": int | None,
            },
            ...
        ]
        """
        ...
