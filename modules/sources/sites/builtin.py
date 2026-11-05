"""The sites a module instance reads when it is not given others: one line to register a site here, nothing else changes."""
from __future__ import annotations

from . import Site
from .animakima import AnimakimaSite
from .animesss import AnimesssSite
from .dunhuatv import DunhuaTvSite
from .pvashow import PvaShowSite
from .ruchime import RuchimeSite
from .vk_channel import VkChannelSite
from .yummyani import YummyAniSite


def builtin_sites() -> list[Site]:
    return [RuchimeSite(), DunhuaTvSite(), AnimakimaSite(), VkChannelSite(), AnimesssSite(), PvaShowSite(), YummyAniSite()]
