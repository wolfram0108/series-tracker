"""What the settings screen of the alignment server sends and shows."""
from __future__ import annotations

from pydantic import BaseModel

from .voices import SettingProblem


class ConformSettingsDraft(BaseModel):
    """The address of the daemon (empty: none) and the work folder as this server names it and as the daemon names the
    same folder; a save replaces all three."""

    url: str
    work_dir: str
    work_dir_remote: str


class ConformSettingsView(ConformSettingsDraft):
    """The reply of `conform.settings.get`: what applies now, every value stated. `clear_finished` is read here and
    written by the pipeline flag of the debug tab."""

    clear_finished: bool


class ConformSettingsSaved(BaseModel):
    """The reply of `conform.settings.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: list[SettingProblem]
    settings: ConformSettingsView
