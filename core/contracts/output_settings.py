"""The levers of the output of a series, set on the series, a season or an episode.

A record is kept in domain terms (numbers the constructor gave, the name of a voice-over group, a source of the series,
a kind of player), so that it outlives a new reading of a table. No record means "as the level above".

A variant is one place a voice-over of an episode can be had from: a voice-over group at a player of a source. The output
of an episode is the variants of its voice-overs; a lever tells which of them is the reference, which take part, which
is preferred.
"""
from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# The levers every consumer reads by name; the registry that gives them meaning is the composition module's.
REFERENCE_CHOICE = "reference.choice"
VARIANT_ALLOW = "variant.allow"
VOICE_TAKE = "voice.take"
VOICE_PLAYER = "voice.player"


class Level(StrEnum):
    DEFAULT = "default"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"


class TargetKind(StrEnum):
    """What a lever's value is about: nothing in particular, a voice-over group, or a variant of one."""

    NONE = "none"
    GROUP = "group"
    VARIANT = "variant"


class ValueShape(StrEnum):
    """The shape of the value of a lever: a flag, the pick of a variant of a group (or auto), or the pick of a
    reference (a variant with its group, or auto). A window draws a lever by its target and its shape."""

    FLAG = "flag"
    VARIANT_PICK = "variant_pick"
    REFERENCE_PICK = "reference_pick"


_GROUP_TARGET = re.compile(r"group:(.+)", re.DOTALL)


def group_target(name: str) -> str:
    return f"group:{name}"


def variant_target(group: str, source_id: int, player: str) -> str:
    """The target of a variant: a JSON array, so that no name, however odd, can be taken for a part of another."""
    return "variant:" + json.dumps([group, source_id, player], ensure_ascii=False, separators=(",", ":"))


def parse_variant_target(target: str) -> tuple[str, int, str] | None:
    """(group, source id, player) of a target made by `variant_target`; None for anything else."""
    if not target.startswith("variant:"):
        return None
    try:
        found = json.loads(target[len("variant:"):])
    except ValueError:
        return None
    if (not isinstance(found, list) or len(found) != 3 or not isinstance(found[0], str) or not found[0]
            or isinstance(found[1], bool) or not isinstance(found[1], int) or not isinstance(found[2], str)
            or not found[2]):
        return None
    group, source_id, player = found
    return (group, source_id, player) if variant_target(group, source_id, player) == target else None


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


class LeverSpec(BaseModel):
    """What a lever is, as the registry of the composition module has it: its name, what its value is about, the
    shape of its value, its default and the module that acts on it. A window needs nothing else to draw it."""

    name: str
    target: TargetKind
    shape: ValueShape
    default: Any = None
    consumer: str


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
