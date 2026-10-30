"""What the settings screen of the alignment server sends and shows."""
from __future__ import annotations

from pydantic import BaseModel

from .voices import SettingProblem


class ConformSettingsDraft(BaseModel):
    """The address of the daemon (empty: none), the work folder as this server names it and as the daemon names the
    same folder, and whether a finished job leaves the daemon's history on its own; a save replaces all four."""

    url: str
    work_dir: str
    work_dir_remote: str
    clear_finished: bool = False


class ConformSettingsView(ConformSettingsDraft):
    """The reply of `conform.settings.get`: what applies now, every value stated."""

    clear_finished: bool


class ConformSettingsSaved(BaseModel):
    """The reply of `conform.settings.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: list[SettingProblem]
    settings: ConformSettingsView
