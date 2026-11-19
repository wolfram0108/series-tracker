"""A compilation of a season: one video of a source that holds several episodes, as the output sees it (stage 12).

A compilation is named by a key in domain terms — the voice-over group, the source, the player, the season and the range of
episodes — so that a choice the user made outlives a new reading of the table (a row key may change, the range does not).
An episode stands in at most one compilation: its video is then cut out of the compilation by the alignment service, on
the timeline of the single episode chosen as the reference (REF 2), and that cut is the video of the episode.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .. import _native

def compilation_key(group: str, source_id: int, player: str, season: int, start: int, end: int) -> str:
    """The key of a compilation: a JSON array, so that no name, however odd, can be taken for a part of another. Made and
    read by the core (native/contracts)."""
    return _native.compilation_key(group, source_id, player, season, start, end)


def parse_compilation_key(key: object) -> tuple[str, int, str, int, int, int] | None:
    """(group, source id, player, season, start, end) of a key made by `compilation_key`; None for anything else."""
    return _native.parse_compilation_key(key) if isinstance(key, str) else None


class Absence(StrEnum):
    """Why an episode a compilation covers does not stand in it: it stands in another one (`elsewhere`), the compilation
    is no better than the episode's own single video (`not_better`), the episode has no single video to cut it by
    (`no_single`), the user took it out of every compilation (`own`), or the whole compilation is switched off (`off`)."""

    ELSEWHERE = "elsewhere"
    NOT_BETTER = "not_better"
    NO_SINGLE = "no_single"
    OWN = "own"
    OFF = "off"


class CompilationRef(BaseModel):
    """The compilation an episode stands in, whole, so that the reference and the plan need no second request: its key,
    the row of the layout whose video it is (the best of the rows with that key), and what the file name takes from it."""

    key: str
    source_id: int
    row_key: str
    player: str
    voice_group: str = ""
    height: int | None = None
    start: int
    end: int


class CompilationEpisode(BaseModel):
    """An episode a compilation covers: whether it stands in it, else why not (and where it stands instead)."""

    episode: int
    member: bool = False
    absence: Absence | None = None
    elsewhere: str | None = None
    # The episode's choice is the user's own (the lever on the episode), not the rule's.
    own: bool = False


class CompilationView(BaseModel):
    """A compilation of a season as the window of the output draws it: the block of its episodes. `index` is its place
    among the compilations of the season (the colour and the letter of the window), `take` whether it is switched on."""

    season: int
    index: int
    ref: CompilationRef
    take: bool = True
    episodes: list[CompilationEpisode] = Field(default_factory=list)
