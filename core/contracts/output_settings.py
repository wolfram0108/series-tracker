"""The levers of the output of a series, set on the series, a season or an episode.

A record is kept in domain terms (numbers the constructor gave, the name of a voice-over group, a source of the series,
a kind of player), so that it outlives a new reading of a table. No record means "as the level above".
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Level(StrEnum):
    DEFAULT = "default"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"


def player_target(source_id: int, player: str) -> str:
    return f"source:{source_id}/player:{player}"


def group_target(name: str) -> str:
    return f"group:{name}"


class _Scope(BaseModel):
    """A level: the series alone, one season of it, or one episode of a season."""

    series_id: int
    season: int | None = None
    episode: int | None = None

    @model_validator(mode="after")
    def _episode_has_a_season(self):
        if self.episode is not None and self.season is None:
            raise ValueError("an episode is set within a season")
        return self

    @property
    def level(self) -> Level:
        if self.episode is not None:
            return Level.EPISODE
        return Level.SEASON if self.season is not None else Level.SERIES


class SettingRecord(_Scope):
    lever: str
    target: str | None = None
    value: Any = None


class SettingsSet(SettingRecord):
    """The command `composition.settings.set`."""


class SettingsReset(_Scope):
    """The command `composition.settings.reset`: every lever of the level goes back to the level above."""


class EffectiveLever(BaseModel):
    """The value a lever has for an episode and the level it comes from."""

    lever: str
    target: str | None = None
    value: Any = None
    level: Level


class EpisodeSettings(BaseModel):
    season: int
    episode: int
    levers: list[EffectiveLever] = Field(default_factory=list)
