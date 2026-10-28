"""Base contract for web video providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


class StreamUnavailable(RuntimeError):
    """The provider owns the URL but cannot produce a stream for it now."""

    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


class BaseVideoProvider(ABC):
    """Interface for web video providers (Ruchime, etc.)."""

    name: str
    domains: tuple[str, ...]
    # True when parse_release lists the voice-overs of a release with the episodes of each
    # (`sources`, `selected_source`); the voices module asks only such providers.
    has_voice_catalog: bool = False

    @abstractmethod
    def match_url(self, url: str) -> bool:
        """Check if provider handles the given URL."""
        ...

    @abstractmethod
    def canonical_url(self, url: str) -> str:
        """Return the canonical URL for the release."""
        ...

    @abstractmethod
    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        """Parse release page and probe episodes for titles and resolutions.

        Returns:
            {
                "canonical_url": str,
                "title": {"ru": str | None, "en": str | None},
                "titles": list[str],
                "resolution": int | None,
                "videos": list[dict],
                "episodes_count": int,
                "provider": str,
                "source_type": "web_video",
                "releases": list[dict],
            }
        """
        ...

    @abstractmethod
    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict,
                            episodes: list[int] | None = None,
                            progress=None) -> dict:
        """Scan episodes and return video facts for scan planner.

        `episodes` limits page fetches to the given numbers (rules already
        applied); None means every episode of the release.

        Returns:
            {
                "videos": [
                    {"title": str, "url": str, "publication_date": str,
                     "resolution": int | None},
                    ...
                ],
                "players": dict,
            }
        """
        ...

    async def resolve_stream(self, url: str, http: httpx.AsyncClient,
                             max_height: int | None = None) -> dict | None:
        """Optionally resolve a direct stream URL and required HTTP headers.

        `max_height` asks for the best stream not above that frame height (the
        lowest one above it when nothing fits); a provider with one stream ignores it.

        Returns:
            {"stream_url": str, "headers": dict} or None if not handled.

        Raises:
            StreamUnavailable: the URL is the provider's own page reference
                and no stream could be produced for it.
        """
        return None
