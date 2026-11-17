"""The queues of a level as a window lists them: the reference, the markup reference and the voice-over of each group, in
the order the core tries their variants (`core.variant_queue`), with what the levers of the level say of each variant.

Pure functions over the lines of the level and its levers: the same rule and the same levers the planner reads, so the
order shown is the order taken. A variant is listed where the table of what is allowed lets the user put it into the
queue: the reference and the markup reference take a variant that has a single line that plays, a voice-over one that
plays at all.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from core.contracts import (MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME, REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME,
                            SUBTITLES_ALLOW, SUBTITLES_ORDER, VOICE_ALLOW,
                            VOICE_ORDER, VOICE_TAKE, CompositionRow, EffectiveLever, GroupQueue, Level, QueueRow, QueueView,
                            RowState, group_target, variant_target)
from core.subtitle_queue import named_of, ordered
from core.variant_queue import VOICE_RANKING, Candidate, Order, Same, ranked, ranked_singles

from .project import spans

Variant = tuple[str, int, str]  # (group, source id, player)


class _Level:
    """The lines of a level by variant and its levers as it inherits them."""

    def __init__(self, lines: Sequence[CompositionRow], levers: Iterable[EffectiveLever], episode: int | None) -> None:
        self.episode = episode
        self.by_variant: dict[Variant, list[tuple[int, CompositionRow]]] = {}
        for position, row in enumerate(lines):
            self.by_variant.setdefault((row.voice_label, row.source_id, row.player), []).append((position, row))
        self.values = {(each.lever, each.target): each for each in levers}

    def value(self, lever: str, target: str | None, default: Any) -> tuple[Any, Level]:
        found = self.values.get((lever, target))
        return (found.value, found.level) if found is not None else (default, Level.DEFAULT)

    def candidates(self, single: bool, group: str | None = None) -> list[Candidate]:
        found = []
        for key, items in self.by_variant.items():
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

    def row_of(self, candidate: Candidate, allow: str) -> QueueRow:
        key = (candidate.row.voice_label, candidate.row.source_id, candidate.row.player)
        playing = [row for _, row in self.by_variant[key] if row.state == RowState.PLAYABLE]
        allowed, level = self.value(allow, variant_target(*key), True)
        current = {number for row in playing if not row.stale for number in _episodes(row, self.episode)}
        stale = {number for row in playing if row.stale for number in _episodes(row, self.episode)} - current
        return QueueRow(group=key[0], source_id=key[1], player=key[2],
                        player_label=next((row.player_label for _, row in self.by_variant[key] if row.player_label), key[2]),
                        height=max((row.height or 0 for row in playing), default=0) or None,
                        episodes=spans(number for row in playing for number in _episodes(row, self.episode)),
                        stale=spans(stale), allowed=allowed is not False, allowed_level=level)

    def single_queue(self, order: Order, order_lever: str, allow_lever: str, same_lever: str) -> QueueView:
        """A queue of single videos (the reference, the markup reference) by its order, its flags and the variants the user
        declared equal: those that stand in one place of the queue carry the number of their group."""
        order_value, order_level = self.value(order_lever, None, [])
        same_value, same_level = self.value(same_lever, None, [])
        same = Same.of(same_value)
        found = ranked_singles(self.candidates(single=True), order, _named(order_value, True), same)
        rows = [self.row_of(each, allow_lever) for each in found]
        # A group that has two variants or more on the level is numbered, by where it first stands in the queue.
        listed = [(row.group, row.source_id, row.player) for row in rows]
        numbers: dict[int, int] = {}
        for variant in listed:
            group = same.number_of(variant)
            if group is not None and group not in numbers and sum(1 for each in listed if each in same.members(variant)) > 1:
                numbers[group] = len(numbers) + 1
        rows = [row.model_copy(update={"same": numbers.get(same.number_of(variant))}) for row, variant in zip(rows, listed)]
        return QueueView(rows=rows, order_level=order_level, same_level=same_level)


def build(lines: Sequence[CompositionRow], levers: Iterable[EffectiveLever], order: Order, groups: Sequence[str],
          episode: int | None = None) -> tuple[QueueView, list[GroupQueue]]:
    """The queue of the reference and one queue per group (in the order of `groups`; a group with no line is left out).

    `lines` are the lines of the level in the order of the layout, `levers` the levers of the level as it inherits them,
    `episode` the episode of an episode level (the episodes of a line are cut to it)."""
    level = _Level(lines, levers, episode)
    reference = level.single_queue(order, REFERENCE_ORDER, REFERENCE_ALLOW, REFERENCE_SAME)
    queues: list[GroupQueue] = []
    # A voice-over is queued only for a group: the rows of an unknown voice-over are taken into no work.
    present = {key[0] for key, items in level.by_variant.items() if all(row.grouped for _, row in items)}
    for group in [each for each in groups if each in present] + sorted(present - set(groups)):
        target = group_target(group)
        order_value, order_level = level.value(VOICE_ORDER, target, [])
        take, take_level = level.value(VOICE_TAKE, target, True)
        group_queue = ranked(level.candidates(single=False, group=group), order, VOICE_RANKING, _named(order_value, False),
                             lambda each: (each.row.source_id, each.row.player))
        queues.append(GroupQueue(group=group, rows=[level.row_of(each, VOICE_ALLOW) for each in group_queue],
                                 order_level=order_level, take=take is not False, take_level=take_level))
    return reference, queues


def subtitles(lines: Sequence[CompositionRow], levers: Iterable[EffectiveLever], sources: dict[int, int],
              episode: int | None = None) -> QueueView:
    """The subtitles of a level in the order of the container (core.subtitle_queue): every variant the lines have, the ones
    the level leaves out marked. `lines` are the rows of subtitles of the level in the order of the layout, `sources` the
    place of every source of the series."""
    level = _Level(lines, levers, episode)
    named, order_level = level.value(SUBTITLES_ORDER, None, [])
    found = [(key, sources.get(key[1], len(sources)), min(position for position, _ in items))
             for key, items in level.by_variant.items()]
    rows = []
    for key in ordered(found, named_of(named)):
        items = [row for _, row in level.by_variant[key]]
        allowed, allowed_level = level.value(SUBTITLES_ALLOW, variant_target(*key), True)
        current = {number for row in items if not row.stale for number in _episodes(row, episode)}
        stale = {number for row in items if row.stale for number in _episodes(row, episode)} - current
        first = items[0]
        rows.append(QueueRow(group=key[0], source_id=key[1], player=key[2],
                             player_label=next((row.player_label for row in items if row.player_label), key[2]),
                             episodes=spans(number for row in items for number in _episodes(row, episode)),
                             stale=spans(stale), allowed=allowed is not False, allowed_level=allowed_level,
                             language=first.language, subtitle_type=first.subtitle_type))
    return QueueView(rows=rows, order_level=order_level)


def same_groups(rows: Iterable[QueueRow]) -> list[list[dict]]:
    """The groups of equal variants a queue shows, as the value of a lever `….same`: one list for every number, in the order
    of the numbers, each variant as the order of the reference names it."""
    groups: dict[int, list[dict]] = {}
    for row in rows:
        if row.same is not None:
            groups.setdefault(row.same, []).append({"group": row.group, "source_id": row.source_id, "player": row.player})
    return [groups[number] for number in sorted(groups)]


def follows_reference(levers: Iterable[EffectiveLever]) -> bool:
    """The markup reference follows the reference while no level of the chain has its own order (stage 14): the lever
    then has only its default."""
    return not any(each.lever == MARKUP_ORDER and each.level != Level.DEFAULT for each in levers)


def markup(lines: Sequence[CompositionRow], levers: Iterable[EffectiveLever], order: Order,
           episode: int | None = None) -> tuple[QueueView, bool]:
    """The queue of the markup reference and whether it follows the reference: then it is the queue of the reference
    itself, with its order and its flags, as the window shows it and as the first edit of the markup copies it."""
    levers = list(levers)
    level = _Level(lines, levers, episode)
    if follows_reference(levers):
        return level.single_queue(order, REFERENCE_ORDER, REFERENCE_ALLOW, REFERENCE_SAME).model_copy(
            update={"order_level": Level.DEFAULT, "same_level": Level.DEFAULT}), True
    return level.single_queue(order, MARKUP_ORDER, MARKUP_ALLOW, MARKUP_SAME), False


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
