from .base import BaseVideoProvider, StreamUnavailable
from .dunhuatv import DunhuaTvProvider
from .registry import VideoProviderRegistry


_DEFAULT_REGISTRY: VideoProviderRegistry | None = None


def get_default_registry() -> VideoProviderRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = VideoProviderRegistry()
        _DEFAULT_REGISTRY.register(DunhuaTvProvider())
    return _DEFAULT_REGISTRY



__all__ = ["BaseVideoProvider", "StreamUnavailable", "VideoProviderRegistry", "DunhuaTvProvider",
           "get_default_registry"]
