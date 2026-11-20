"""What the screen of the provider of external sources (the tab «Chines» of the extensions) sends and shows."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .frozen import Frozen
from .voices import SettingProblem


class ProviderSettingsDraft(BaseModel):
    """The address of the provider (empty: none) and its token: `None` keeps the stored one, an empty text clears it."""

    url: str
    token: str | None = None


class ProviderSite(Frozen):
    """A site of the provider's manifest: how many sources of the series are its titles, how many lines of the order of sources
    are its."""

    name: str
    label: str
    sources: int = 0
    lines: int = 0


class ProviderView(Frozen):
    """The reply of `sources.provider.get` and the body of `sources.provider.changed`: everything the tab shows, every value
    stated. `state` is that of the socket (`none`: no address); `reason` is why it is not up, as the event of the state gives
    it. The manifest facts and the sites are those of the last manifest, kept while the provider is down.
    `downloads_dir` is the folder the provider writes the files of the series to (the one inside the work folder of the
    alignment). `seq` grows with every view: a screen never takes a view older than the one it shows."""

    url: str
    has_token: bool
    state: Literal["none", "connecting", "up", "down", "incompatible"]
    reason: str | None = None
    provider: str | None = None
    version: str | None = None
    protocol: int | None = None
    revision: str | None = None
    sites: tuple[ProviderSite, ...] = Field(default_factory=tuple)
    downloads_dir: str = ""
    seq: int = 0


class ProviderSaved(Frozen):
    """The reply of `sources.provider.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: tuple[SettingProblem, ...]
    provider: ProviderView
