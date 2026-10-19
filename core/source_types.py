"""Source type classifications."""
from __future__ import annotations

VIDEO_SOURCE_TYPES = frozenset({"vk_video", "web_video"})


def is_video_source(source_type: str | None) -> bool:
    """Return True if source_type represents a video-based source (VK or web video)."""
    return source_type in VIDEO_SOURCE_TYPES
