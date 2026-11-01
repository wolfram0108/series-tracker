"""The sites a module instance reads when it is not given others: one line to register a site here, nothing else changes."""
from __future__ import annotations

from . import Site
from .dunhuatv import DunhuaTvSite
from .ruchime import RuchimeSite
from .vk_channel import VkChannelSite


def builtin_sites() -> list[Site]:
    return [RuchimeSite(), DunhuaTvSite(), VkChannelSite()]
