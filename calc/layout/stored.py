"""The layout of a series as its owner last wrote it: the input of every function of `calc.layout` that reads it."""
from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import CompositionRow, SettingRecord, SourceSummary


@dataclass(frozen=True)
class StoredSource:
    """What the window shows about a source and the canonical address of its page (None before the first answer)."""

    source_id: int
    position: int
    table_url: str | None
    summary: SourceSummary


@dataclass
class Stored:
    """A series as the module last wrote it; a series never counted has revision 0 and nothing else."""

    rev: int = 0
    rows: list[CompositionRow] = field(default_factory=list)
    # The rows of subtitles, apart from the rows of sound (DESIGN.md 7.1.1); one table keeps both.
    subtitles: list[CompositionRow] = field(default_factory=list)
    sources: list[StoredSource] = field(default_factory=list)
    numbering: dict[str, int] = field(default_factory=dict)
    records: list[SettingRecord] = field(default_factory=list)
    # season -> the voice-over groups in the order of the tracks of the container (stage 11)
    orders: dict[int, list[str]] = field(default_factory=dict)
    # voice-over group -> the user's name of its track in the containers
    titles: dict[str, str] = field(default_factory=dict)


def source_order(stored: Stored) -> dict[int, int]:
    """The place of every source of the series, for the rule of compilations."""
    return {each.source_id: place for place, each in enumerate(stored.sources)}
