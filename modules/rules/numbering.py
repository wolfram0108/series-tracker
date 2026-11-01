"""The numbers the constructor settles beyond what one line says: the default season and the first part.

Both are conventions of the files the constructor numbers, so they are part of its answer: a window that tests a
profile and the layout that is built from it read the same numbers. No other module computes one.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from core import season as season_rule
from core.contracts import RuleLine, RuleResult
from core.voice_names import fold_name

# A season that neither the rules nor the card of the series gave is season 1, as the plan and the file name read it.
DEFAULT_SEASON = 1


def season_of(series_season: str | int | None, extracted: int | None) -> int:
    """The season of a numbered line: the one the rules extracted, else the card's, else the default."""
    found = season_rule.resolve(series_season, extracted)
    return found if found is not None else DEFAULT_SEASON


def _span(result: RuleResult) -> tuple[int, int] | None:
    """The episodes a line names: a single episode wins over a range, a range without an end ends where it starts."""
    if result.excluded:
        return None
    if result.episode is not None:
        return result.episode, result.episode
    if result.start is not None:
        return result.start, result.end if result.end is not None else result.start
    return None


def _slot(line: RuleLine, result: RuleResult, span: tuple[int, int]) -> tuple:
    """Lines of one player and one voice-over (the assigned name beats the one the table gave) name one episode."""
    assigned = result.voiceover if result.voiceover and result.voiceover.strip() else line.voice
    return line.player, fold_name(assigned), result.season, span[0]


def settle_parts(lines: Sequence[RuleLine], results: Mapping[str, RuleResult]) -> dict[str, RuleResult]:
    """A single episode without a part is part 1 where its slot has single episodes with parts.

    A compilation neither makes a slot nor takes part 1. The input is left as it is: the settled answers are returned."""
    spans = {line.key: _span(results[line.key]) for line in lines if line.key in results}
    parted = {_slot(line, results[line.key], spans[line.key]) for line in lines
              if spans.get(line.key) and spans[line.key][0] == spans[line.key][1] and results[line.key].part is not None}
    settled = dict(results)
    for line in lines:
        span = spans.get(line.key)
        if span and span[0] == span[1] and results[line.key].part is None and _slot(line, results[line.key], span) in parted:
            settled[line.key] = results[line.key].model_copy(update={"part": 1})
    return settled
