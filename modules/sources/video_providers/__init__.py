from .base import BaseVideoProvider
from .registry import VideoProviderRegistry
from .ruchime import RuchimeProvider


def get_default_registry() -> VideoProviderRegistry:
    registry = VideoProviderRegistry()
    registry.register(RuchimeProvider())
    return registry


__all__ = ["BaseVideoProvider", "VideoProviderRegistry", "RuchimeProvider", "get_default_registry"]
