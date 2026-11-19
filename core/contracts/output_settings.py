"""The levers of the output of a series, set on the series, a season or an episode.

A record is kept in domain terms (numbers the constructor gave, the name of a voice-over group, a source of the series,
a kind of player), so that it outlives a new reading of a table. No record means "as the level above".

A variant is one place a voice-over of an episode can be had from: a voice-over group at a player of a source. The output
of an episode is the variants of its voice-overs. They are tried in queues — one for the reference, one for the
voice-over of each group — in the order of `core.variant_queue`; a lever puts the variants the user named first, in his
order, and a flag of a variant leaves it out of one queue.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .. import _native

from .compilation import CompilationRef


# The levers every consumer reads by name; the registry that gives them meaning is the composition module's.
REFERENCE_ORDER = "reference.order"
REFERENCE_ALLOW = "reference.allow"
VOICE_ORDER = "voice.order"
VOICE_ALLOW = "voice.allow"
VOICE_TAKE = "voice.take"
# Stage 12: the compilation an episode stands in (its key, or "" for none), and whether a compilation takes episodes at all.
COMPILATION_MEMBER = "compilation.member"
COMPILATION_TAKE = "compilation.take"
# Stage 14: the queue of the markup reference — the variant whose single video gives an episode its timeline. With no
# `markup.order` on the episode's chain of levels the markup reference follows the reference.
MARKUP_ORDER = "markup.order"
MARKUP_ALLOW = "markup.allow"
# Variants the user declared equal (Р-99): the variants of a group take one place in the queue of the reference (of the
# markup reference), so that what the file holds stays while it is as good as the queue's first.
REFERENCE_SAME = "reference.same"
MARKUP_SAME = "markup.same"
# Stage 15 (DESIGN.md 7.1.1): the order of the subtitles of the container (the first variant is its first subtitles track)
# and the flag of a variant that leaves it out; set on the series, a season and an episode. How a track lies in the
# container is no lever: the first full track taken is the default one, the first of signs the forced one.
SUBTITLES_ORDER = "subtitles.order"
SUBTITLES_ALLOW = "subtitles.allow"


class Level(StrEnum):
    DEFAULT = "default"
    SERIES = "series"
    SEASON = "season"
    COMPILATION = "compilation"
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
    COMPILATION = "compilation"
    # Groups of variants (a list of lists), as a queue's variants the user declared equal.
    VARIANT_SETS = "variant_sets"


class Queue(StrEnum):
    """The queue a lever acts on: the reference of an episode, or the voice-over of a group. A window draws the
    levers of a queue on its tab."""

    REFERENCE = "reference"
    VOICE = "voice"
    COMPILATION = "compilation"
    MARKUP = "markup"
    SUBTITLES = "subtitles"


# The forms a window knows how to draw. A lever of another form is refused when it is registered, so that no registered
# lever can be missing from a window; a form joins by a control in the window (web/src/lib/output.ts: RENDERED_FORMS)
# and a line here.
LEVER_FORMS: frozenset[tuple[TargetKind, ValueShape]] = frozenset({
    (TargetKind.NONE, ValueShape.VARIANT_ORDER), (TargetKind.GROUP, ValueShape.VARIANT_ORDER),
    (TargetKind.GROUP, ValueShape.FLAG), (TargetKind.VARIANT, ValueShape.FLAG),
    (TargetKind.NONE, ValueShape.FLAG), (TargetKind.NONE, ValueShape.COMPILATION), (TargetKind.NONE, ValueShape.VARIANT_SETS)})

# The targets of levers are made and read by the core (native/contracts): one writing of each, in one place.
def group_target(name: str) -> str:
    return _native.group_target(name)


def variant_target(group: str, source_id: int, player: str) -> str:
    """The target of a variant: a JSON array, so that no name, however odd, can be taken for a part of another."""
    return _native.variant_target(group, source_id, player)


def parse_variant_target(target: str) -> tuple[str, int, str] | None:
    """(group, source id, player) of a target made by `variant_target`; None for anything else."""
    return _native.parse_variant_target(target)


def parse_group_target(target: str) -> str | None:
    """The group name of a target made by `group_target`; None for anything else."""
    return _native.parse_group_target(target)


class _Scope(BaseModel):
    """A level: the series alone, one season of it, one compilation of a season (its key), or one episode of a season."""

    series_id: int
    season: int | None = None
    episode: int | None = None
    compilation: str | None = None

    @model_validator(mode="after")
    def _episode_has_a_season(self):
        if self.episode is not None and self.season is None:
            raise ValueError("an episode is set within a season")
        if self.compilation is not None and (self.season is None or self.episode is not None):
            raise ValueError("a compilation is set within a season, not on an episode")
        return self

    @property
    def level(self) -> Level:
        if self.episode is not None:
            return Level.EPISODE
        if self.compilation is not None:
            return Level.COMPILATION
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


class SettingsSetMany(BaseModel):
    """The command `composition.settings.set_many`: several levers of one series set in one write — one revision, one
    event, one recount of the plan. The items are applied in order; one refused item refuses them all."""

    series_id: int
    items: list[SettingsSet] = Field(min_length=1)

    @model_validator(mode="after")
    def _items_are_of_the_series(self):
        if any(item.series_id != self.series_id for item in self.items):
            raise ValueError("every item is a lever of the series of the command")
        return self


class SettingsReset(_Scope):
    """The command `composition.settings.reset`: the levers of the level go back to the level above.

    Without `lever` every lever of the level goes back; with `lever` (and `target` for a lever that has one)
    only that one does; with `queue` every lever of that queue does (its order and the flags of its variants)."""

    lever: str | None = None
    target: str | None = None
    queue: Queue | None = None

    @model_validator(mode="after")
    def _lever_or_queue(self):
        if self.queue is not None and self.lever is not None:
            raise ValueError("a reset names a lever or a queue, not both")
        return self


class EffectiveLever(BaseModel):
    """The value a lever has for an episode and the level it comes from."""

    lever: str
    target: str | None = None
    value: Any = None
    level: Level


class EpisodeSettings(BaseModel):
    """The levers of an episode, and the compilation it stands in (stage 12): the level between its season and itself."""

    season: int
    episode: int
    levers: list[EffectiveLever] = Field(default_factory=list)
    compilation: CompilationRef | None = None


class LevelSettings(BaseModel):
    """The levers as they stand on the series (season None) or on one season: what the header of the level shows
    and what its episodes inherit."""

    season: int | None = None
    compilation: str | None = None
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
    # The episodes of `episodes` its source did not give in its last read: they stand from an earlier read (`1–8`).
    stale: str = ""
    allowed: bool = True
    allowed_level: Level = Level.DEFAULT
    # The number of the group of equal variants the row stands in (1, 2… in the order the groups first appear in the queue);
    # none for a variant that equals no other one the level lists.
    same: int | None = None
    # A variant of subtitles: the language of its tracks and their type (full, signs), as its rows say; and how its track
    # lies in the container (core.subtitle_queue.flags: the first full one taken default, the first of signs forced).
    language: str = ""
    subtitle_type: str = ""
    default: bool = False
    forced: bool = False


class QueueView(BaseModel):
    """The variants of one queue in the order they are tried; `order_level` is the level the user's order comes from."""

    rows: list[QueueRow] = Field(default_factory=list)
    order_level: Level = Level.DEFAULT
    # The level the groups of equal variants come from (the levers `reference.same`, `markup.same`).
    same_level: Level = Level.DEFAULT


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
    compilation: str | None = None
    reference: QueueView = Field(default_factory=QueueView)
    groups: list[GroupQueue] = Field(default_factory=list)
    # The queue of the markup reference (stage 14); while `markup_follows` it is the queue of the reference itself: no
    # level of the chain has an order of its own, and the timeline is the video the reference takes.
    markup: QueueView = Field(default_factory=QueueView)
    markup_follows: bool = True
    # The subtitles of the level (DESIGN.md 7.1.1): every allowed variant is a track of the container, in this order; the
    # group of a row is the name of its subtitles. A season has its own over the series'; a compilation shows the series'.
    subtitles: QueueView = Field(default_factory=QueueView)
