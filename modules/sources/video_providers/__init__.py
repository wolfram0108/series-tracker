from .base import BaseVideoProvider, StreamUnavailable
from .registry import VideoProviderRegistry
from .ruchime import RuchimeProvider


_DEFAULT_REGISTRY: VideoProviderRegistry | None = None


def get_default_registry() -> VideoProviderRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = VideoProviderRegistry()
        _DEFAULT_REGISTRY.register(RuchimeProvider())
    return _DEFAULT_REGISTRY



__all__ = ["BaseVideoProvider", "StreamUnavailable", "VideoProviderRegistry", "RuchimeProvider",
           "get_default_registry"]
