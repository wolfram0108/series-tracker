"""The layout of a series as a pure function of what its sources gave and what the constructor answered.

The count is the compiled one of `core.native`: the tables and the answers are read in place, the rows of the layout are
made as columns, and every reader asks them what it needs (the episodes they cover, what changed between two layouts) —
no object is made of each row. Nothing here touches a site, the clock, a database or chance: equal inputs give equal
rows. Every number of a row comes from the constructor's answer to the line of that row; this module never computes one.
What is left here is the order of the voice-overs of a season, which the user keeps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from core import native
from core.contracts import RulesApplyReply, SeasonVoiceOrder, SeriesSource, SourceSummary, StoredTable, VoiceSpan

# One template for the name of a voice-over that belongs to no user group (the count names them so: core.native).
UNKNOWN_VOICE_LABEL = "Неизвестная озвучка {number}"
_UNKNOWN = re.compile("^" + re.escape(UNKNOWN_VOICE_LABEL).replace(re.escape("{number}"), r"\d+") + "$")


@dataclass(frozen=True)
class SourceInput:
    """A source of the series with what the other modules told about it.

    `reply` is None while the source has no profile; `failure` is a reason when the page could not be read, and
    then `table` is the last complete table, which stays in force."""

    source: SeriesSource
    table: StoredTable | None = None
    reply: RulesApplyReply | None = None
    failure: str | None = None
    # The canonical address of the page as `sources` gave it, known before the first table arrives.
    table_url: str | None = None
    # The players switched off in the settings for every source (`voices.players_off`).
    players_off: frozenset[str] = frozenset()

    def given(self) -> dict:
        """The input as the count takes it: the fields of the table and of the answers as they are."""
        return {"source": self.source.model_dump(), "head": self.table.head() if self.table is not None else None,
                "rows": self.table.rows if self.table is not None else None,
                "answers": self.reply.results if self.reply is not None else None, "failure": self.failure,
                "table_url": self.table_url, "everywhere": sorted(self.players_off)}


@dataclass(frozen=True)
class Projection:
    """`unknown_voices` maps the folded name of a voice-over to its number; it only grows."""

    rows: native.Rows
    sources: list[SourceSummary]
    unknown_voices: dict[str, int]
    # The rows of subtitles (DESIGN.md 7.1.1): kept apart, so that no consumer of the rows of sound takes them for a voice-over.
    subtitles: native.Rows = field(default_factory=native.Rows)


def lines_for(item: SourceInput) -> bytes:
    """What the constructor is asked about the source: one line with its key for every row the choices of the user leave
    in play (a player switched off in the settings or for the source, another season of a catalog), as columns."""
    return native.lines(item.given())


def voice_names(inputs: Sequence[SourceInput]) -> list[str]:
    """The distinct voice-over names of the rows the constructors answered: the names the groups are asked about."""
    return native.voice_names([each.given() for each in inputs])


def project(inputs: Sequence[SourceInput], groups: Mapping[str, str | None], unknown: Mapping[str, int]) -> Projection:
    """Rows of all sources in source order, a summary of every source, and the numbering of unknown voice-overs.

    `groups` maps a voice-over name to the user group it belongs to, None for a name in no group."""
    sound, subtitles, summaries, numbering = native.project([each.given() for each in inputs], dict(groups),
                                                            list(unknown.items()))
    return Projection(sound, [SourceSummary.model_validate_json(each) for each in summaries], dict(numbering), subtitles)


# --- the order of the voice-overs of a season (stage 11) --------------------------------------------------------------

def season_orders(previous: Mapping[int, Sequence[str]], rows: native.Rows,
                  renames: Mapping[str, str]) -> dict[int, list[str]]:
    """season -> the voice-over groups in the order of the tracks of the container.

    What is there keeps its place: a group renamed keeps the place of its old name, two groups that became one keep the
    higher of their places, a group that has no row now keeps its place for when it comes back. A group new to the season
    takes the end, in the order the layout gives it (the sources in their order, then the rows as they come); only the
    user moves the others."""
    present = dict(rows.season_labels())
    orders: dict[int, list[str]] = {}
    for season in sorted(set(previous) | set(present)):
        kept: list[str] = []
        for label in previous.get(season, ()):
            label = renames.get(label, label)
            # An unknown voice-over is no voice-over of the output: its name is no place in the order of the tracks.
            if label not in kept and not _UNKNOWN.match(label):
                kept.append(label)
        kept += [label for label in present.get(season, []) if label not in kept]
        orders[season] = kept
    return orders


def season_voices(orders: Mapping[int, Sequence[str]], rows: native.Rows) -> list[SeasonVoiceOrder]:
    """The whole order of every season, the one the containers follow: each group with the episodes its rows have now,
    none for a group with no row (its track may still be in the files, which the planner knows, and the window shows
    it there)."""
    episodes = rows.season_episodes()
    return [SeasonVoiceOrder(season=season, voices=[
        VoiceSpan(label=label, episodes=spans(episodes.get((season, label), ()))) for label in orders[season]])
        for season in sorted(orders) if orders[season]]


def spans(numbers: Iterable[int]) -> str:
    """`1–50, 52` of a set of episode numbers."""
    runs: list[list[int]] = []
    for number in sorted(set(numbers)):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(str(first) if first == last else f"{first}–{last}" for first, last in runs)


def reorder(stored: Sequence[str], wanted: Sequence[str]) -> list[str]:
    """The stored order with the groups the window shows put in the user's order in the places they take, the others in
    theirs. The window shows what the rows and the files hold, so a group the order does not know yet takes the end."""
    base = list(stored) + [label for label in wanted if label not in stored]
    chosen, queue = set(wanted), list(wanted)
    return [queue.pop(0) if label in chosen else label for label in base]
