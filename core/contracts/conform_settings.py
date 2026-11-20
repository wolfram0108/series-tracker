"""What the settings screen of the alignment server sends and shows."""
from __future__ import annotations

from pydantic import BaseModel

from .voices import SettingProblem


class ConformParams(BaseModel):
    """How the daemon is asked to align (the fields of its jobs): the meter of the audio layer (`audio_method`), how fast
    the laid sound may follow a drift (`drift_speed_pct`, %/s; None: the daemon's own default), whether the data of an
    episode stays in its cache for a rerun (`keep_tmp`, the checkpoints), whether the silence left in a voice-over where
    something was cut is filled with the reference's sound (`fill_silence`; a job of an episode only), and whether the
    reference cut out of a compilation covers the whole episode or only the stretch the compilation covers
    (`extend_edges`; a job of a compilation only). A save replaces all five."""

    audio_method: str
    drift_speed_pct: float | None = None
    keep_tmp: bool
    fill_silence: bool
    extend_edges: bool


class ConformDomain(BaseModel):
    """What the daemon accepts, as it states it (`GET /conform/settings`): its audio methods and the default, the range
    and the default of the drift speed."""

    audio_methods: list[str]
    audio_default: str
    drift_min: float
    drift_max: float
    drift_default: float


class ConformSettingsDraft(BaseModel):
    """The address of the daemon (empty: none) and the work folder as this server names it and as the daemon names the
    same folder; a save replaces all three."""

    url: str
    work_dir: str
    work_dir_remote: str


class ConformSettingsView(ConformSettingsDraft):
    """The reply of `conform.settings.get`: what applies now, every value stated. `clear_finished` is read here and
    written by the pipeline flag of the debug tab. `domain` is what the daemon accepts, None while it has not said."""

    clear_finished: bool
    params: ConformParams
    domain: ConformDomain | None = None
    # One more with every save, kept with the settings: a screen never takes a view older than the one it shows (the
    # announcement of an earlier save may come after the answer to a later one).
    revision: int = 0


class ConformSettingsSaved(BaseModel):
    """The reply of `conform.settings.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: list[SettingProblem]
    settings: ConformSettingsView
