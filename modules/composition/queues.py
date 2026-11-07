"""The queues of a level as a window lists them: the reference and the voice-over of each group, in the order the core
tries their variants (`core.variant_queue`), with what the levers of the level say of each variant.

Pure functions over the lines of the level and its levers: the same rule and the same levers the choice of the reference
(scan) and the plan of voice-overs (voices) read, so the order shown is the order taken. A variant is listed where the
table of what is allowed lets the user put it into the queue: the reference takes a variant that has a single line that
plays, a voice-over one that plays at all.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from core.contracts import (REFERENCE_ALLOW, REFERENCE_ORDER, VOICE_ALLOW, VOICE_ORDER, VOICE_TAKE, CompositionRow,
                            EffectiveLever, GroupQueue, Level, QueueRow, QueueView, RowState, group_target, variant_target)
from core.variant_queue import REFERENCE_RANKING, VOICE_RANKING, Candidate, Order, ranked

from .project import spans

Variant = tuple[str, int, str]  # (group, source id, player)


def build(lines: Sequence[CompositionRow], levers: Iterable[EffectiveLever], order: Order, groups: Sequence[str],
          episode: int | None = None) -> tuple[QueueView, list[GroupQueue]]:
    """The queue of the reference and one queue per group (in the order of `groups`; a group with no line is left out).

    `lines` are the lines of the level in the order of the layout, `levers` the levers of the level as it inherits them,
    `episode` the episode of an episode level (the episodes of a line are cut to it)."""
    by_variant: dict[Variant, list[tuple[int, CompositionRow]]] = {}
    for position, row in enumerate(lines):
        by_variant.setdefault((row.voice_label, row.source_id, row.player), []).append((position, row))
    values = {(each.lever, each.target): each for each in levers}

    def value(lever: str, target: str | None, default: Any) -> tuple[Any, Level]:
        found = values.get((lever, target))
        return (found.value, found.level) if found is not None else (default, Level.DEFAULT)

    def candidates(single: bool, group: str | None = None) -> list[Candidate]:
        found = []
        for key, items in by_variant.items():
            if group is not None and key[0] != group:
                continue
            playing = [(position, row) for position, row in items
                       if row.state == RowState.PLAYABLE and not (single and row.is_compilation)]
            if playing:
                # The reference ranks lines, so a variant stands as its best line does (the first of the best height);
                # a voice-over ranks its variants by player, then by where their first line stands in the layout.
                place, best = max(playing, key=lambda item: (item[1].height or 0, -item[0]))
                found.append(Candidate(best, place if single else min(position for position, _ in playing)))
        return found

    def row_of(candidate: Candidate, allow: str) -> QueueRow:
        key = (candidate.row.voice_label, candidate.row.source_id, candidate.row.player)
        playing = [row for _, row in by_variant[key] if row.state == RowState.PLAYABLE]
        allowed, level = value(allow, variant_target(*key), True)
        return QueueRow(group=key[0], source_id=key[1], player=key[2],
                        player_label=next((row.player_label for _, row in by_variant[key] if row.player_label), key[2]),
                        height=max((row.height or 0 for row in playing), default=0) or None,
                        episodes=spans(number for row in playing for number in _episodes(row, episode)),
                        allowed=allowed is not False, allowed_level=level)

    order_value, order_level = value(REFERENCE_ORDER, None, [])
    reference_queue = ranked(candidates(single=True), order, REFERENCE_RANKING, _named(order_value, True),
                             lambda each: (each.row.voice_label, each.row.source_id, each.row.player))
    reference = QueueView(rows=[row_of(each, REFERENCE_ALLOW) for each in reference_queue], order_level=order_level)

    queues: list[GroupQueue] = []
    # A voice-over is queued only for a group: the rows of an unknown voice-over are taken into no work.
    present = {key[0] for key, items in by_variant.items() if all(row.grouped for _, row in items)}
    for group in [each for each in groups if each in present] + sorted(present - set(groups)):
        target = group_target(group)
        order_value, order_level = value(VOICE_ORDER, target, [])
        take, take_level = value(VOICE_TAKE, target, True)
        group_queue = ranked(candidates(single=False, group=group), order, VOICE_RANKING, _named(order_value, False),
                             lambda each: (each.row.source_id, each.row.player))
        queues.append(GroupQueue(group=group, rows=[row_of(each, VOICE_ALLOW) for each in group_queue],
                                 order_level=order_level, take=take is not False, take_level=take_level))
    return reference, queues


def _named(value: Any, with_group: bool) -> list[tuple]:
    """The variants of the user's order as the identities of their queue; an item that is not one is skipped."""
    if not isinstance(value, list):
        return []
    named = []
    for each in value:
        if not isinstance(each, dict) or not isinstance(each.get("source_id"), int) or not isinstance(each.get("player"), str):
            continue
        if with_group and isinstance(each.get("group"), str):
            named.append((each["group"], each["source_id"], each["player"]))
        elif not with_group:
            named.append((each["source_id"], each["player"]))
    return named


def _episodes(row: CompositionRow, episode: int | None) -> list[int]:
    if row.episode_start is None:
        return []
    end = row.episode_end if row.episode_end is not None else row.episode_start
    numbers = range(row.episode_start, max(end, row.episode_start) + 1)
    return [episode] if episode is not None and episode in numbers else ([] if episode is not None else list(numbers))
