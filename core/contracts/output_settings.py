"""The levers of the output of a series, set on the series, a season or an episode.

A record is kept in domain terms (numbers the constructor gave, the name of a voice-over group, a source of the series,
a kind of player), so that it outlives a new reading of a table. No record means "as the level above".

A variant is one place a voice-over of an episode can be had from: a voice-over group at a player of a source. The output
of an episode is the variants of its voice-overs. They are tried in queues — one for the reference, one for the
voice-over of each group — in the order of `core.variant_queue`; a lever puts the variants the user named first, in his
order, and a flag of a variant leaves it out of one queue.
"""
from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# The levers every consumer reads by name; the registry that gives them meaning is the composition module's.
REFERENCE_ORDER = "reference.order"
REFERENCE_ALLOW = "reference.allow"
VOICE_ORDER = "voice.order"
VOICE_ALLOW = "voice.allow"
VOICE_TAKE = "voice.take"


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
    """The shape of the value of a lever: a flag, or the order of variants the user put first in a queue (a list,
    empty for the order of the rule). A window draws a lever by its target and its shape."""

    FLAG = "flag"
    VARIANT_ORDER = "variant_order"


class Queue(StrEnum):
    """The queue a lever acts on: the reference of an episode, or the voice-over of a group. A window draws the
    levers of a queue on its tab."""

    REFERENCE = "reference"
    VOICE = "voice"


# The forms a window knows how to draw. A lever of another form is refused when it is registered, so that no registered
# lever can be missing from a window; a form joins by a control in the window (web/src/lib/output.ts: RENDERED_FORMS)
# and a line here.
LEVER_FORMS: frozenset[tuple[TargetKind, ValueShape]] = frozenset({
    (TargetKind.NONE, ValueShape.VARIANT_ORDER), (TargetKind.GROUP, ValueShape.VARIANT_ORDER),
    (TargetKind.GROUP, ValueShape.FLAG), (TargetKind.VARIANT, ValueShape.FLAG)})

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
    queue: Queue


class PlayerVariant(BaseModel):
    """An item of `voice.order`: one player of one source (the group is the target of the lever)."""

    source_id: int
    player: str


class GroupVariant(PlayerVariant):
    """An item of `reference.order`: a voice-over group (its `voice_label`) at one player of one source."""

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


class QueuesGet(_Scope):
    """The query `composition.queues.get`: the queues of a level, as its window lists them."""


class QueueRow(BaseModel):
    """A variant of a queue in its place: a voice-over group at a player of a source, the best height it has on the level,
    the episodes of the level it plays in (`1–10, 12`), and whether the queue takes it (`allowed`, from `allowed_level`)."""

    group: str
    source_id: int
    player: str
    player_label: str
    height: int | None = None
    episodes: str = ""
    allowed: bool = True
    allowed_level: Level = Level.DEFAULT


class QueueView(BaseModel):
    """The variants of one queue in the order they are tried; `order_level` is the level the user's order comes from."""

    rows: list[QueueRow] = Field(default_factory=list)
    order_level: Level = Level.DEFAULT


class GroupQueue(QueueView):
    """The queue of the voice-over of one group, and whether the level takes that voice-over at all (`voice.take`)."""

    group: str
    take: bool = True
    take_level: Level = Level.DEFAULT


class QueuesReply(BaseModel):
    """The queue of the reference and the queue of each group of the level, the groups in the order of the tracks of the
    container (a season, an episode) or by name (the series)."""

    series_id: int
    season: int | None = None
    episode: int | None = None
    reference: QueueView = Field(default_factory=QueueView)
    groups: list[GroupQueue] = Field(default_factory=list)
