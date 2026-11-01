"""The levers of the output of a series, set on the series, a season or an episode.

A record is kept in domain terms (numbers the constructor gave, the name of a voice-over group, a source of the series,
a kind of player), so that it outlives a new reading of a table. No record means "as the level above".
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# The levers every consumer reads by name; the registry that gives them meaning is the composition module's.
REFERENCE_CHOICE = "reference.choice"
REFERENCE_ALLOW = "reference.allow"
VOICE_TAKE = "voice.take"
VOICE_PLAYER = "voice.player"


class Level(StrEnum):
    DEFAULT = "default"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"


_PLAYER_TARGET = re.compile(r"source:(\d+)/player:(.+)", re.DOTALL)
_GROUP_TARGET = re.compile(r"group:(.+)", re.DOTALL)


def player_target(source_id: int, player: str) -> str:
    return f"source:{source_id}/player:{player}"


def group_target(name: str) -> str:
    return f"group:{name}"


def parse_player_target(target: str) -> tuple[int, str] | None:
    """(source id, player) of a target made by `player_target`; None for anything else."""
    found = _PLAYER_TARGET.fullmatch(target)
    if found is None:
        return None
    source_id, player = int(found.group(1)), found.group(2)
    return (source_id, player) if player_target(source_id, player) == target else None


def parse_group_target(target: str) -> str | None:
    """The group name of a target made by `group_target`; None for anything else."""
    found = _GROUP_TARGET.fullmatch(target)
    return found.group(1) if found is not None and group_target(found.group(1)) == target else None


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


class PlayerPick(BaseModel):
    """The value of `voice.player`: one player of one source."""

    source_id: int
    player: str


class ReferenceChoice(PlayerPick):
    """The value of `reference.choice`: a voice-over group (its `voice_label`) at one player of one source."""

    group: str


class SettingRecord(_Scope):
    lever: str
    target: str | None = None
    value: Any = None


class SettingsSet(SettingRecord):
    """The command `composition.settings.set`."""


class SettingsReset(_Scope):
    """The command `composition.settings.reset`: the levers of the level go back to the level above.

    Without `lever` every lever of the level goes back; with `lever` (and `target` for a lever that has one)
    only that one does."""

    lever: str | None = None
    target: str | None = None


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


class LevelSettings(BaseModel):
    """The levers as they stand on the series (season None) or on one season: what the header of the level shows
    and what its episodes inherit."""

    season: int | None = None
    levers: list[EffectiveLever] = Field(default_factory=list)
