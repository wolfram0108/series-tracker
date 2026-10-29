"""What the settings screen of the alignment server sends and shows."""
from __future__ import annotations

from pydantic import BaseModel

from .voices import SettingProblem


class PathPair(BaseModel):
    """A folder of the storage on this server and the same folder as the daemon mounts it."""

    local: str
    daemon: str


class ConformSettingsDraft(BaseModel):
    """The address of the daemon (empty: none) and the map of paths between the two servers; a save replaces both."""

    url: str
    path_map: list[PathPair]


class ConformSettingsView(ConformSettingsDraft):
    """The reply of `conform.settings.get`: what applies now."""


class ConformSettingsSaved(BaseModel):
    """The reply of `conform.settings.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: list[SettingProblem]
    settings: ConformSettingsView
