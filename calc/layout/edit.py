"""What a command of the user does to a layout: the checks of its levers against the table of what is allowed, the records
it writes, and the episodes it touches. Pure steps over the stored layout; the module reads what they need and stores what
they return."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.contracts import (COMPILATION_MEMBER, MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME, REFERENCE_ORDER, REFERENCE_SAME,
                            VOICE_ORDER, SettingRecord, SettingsReset, SettingsSet, VoiceOrderSet, VoiceSettingsView,
                            VoiceTitleSet, availability, parse_group_target, variant_target)
from core.reasons import reason

from . import compilations, project, queues, settings
from .recount import shape
from .stored import Stored, source_order

# The levers of the markup reference: the first edit of one of them on a level that follows the reference makes the queue its own.
MARKUP_LEVERS = (MARKUP_ORDER, MARKUP_ALLOW, MARKUP_SAME)
# The levers whose value is a list of variants: each is put into the queue only where the table of what is allowed lets it.
_PICKED = (REFERENCE_ORDER, MARKUP_ORDER, VOICE_ORDER, REFERENCE_SAME, MARKUP_SAME)
_SINGLES = (REFERENCE_ORDER, MARKUP_ORDER, REFERENCE_SAME, MARKUP_SAME)
_SETS = (REFERENCE_SAME, MARKUP_SAME)


@dataclass(frozen=True)
class Edit:
    """What a change of the records does: the records after it, whether they differ, the episodes whose levers come out
    differently (told to consumers), and whether a window has anything new to show."""

    records: list[SettingRecord]
    changed: bool
    touched: set[tuple[int, int]]
    shown: bool


@dataclass(frozen=True)
class Reordered:
    """The orders of the voice-overs of the series after a season's order was set, and the episodes of that season."""

    orders: dict[int, list[str]]
    touched: set[tuple[int, int]]


@dataclass(frozen=True)
class Renamed:
    """The user's names of the tracks after one was set or taken back, and every episode of the layout."""

    titles: dict[str, str]
    touched: set[tuple[int, int]]


def reads_layout(items: Sequence[SettingsSet]) -> bool:
    """Whether the checks and the writes of the items need the stored layout (`checked`, `writes_of`)."""
    return any(_picks(record) or _takes_compilation(record) or record.lever in MARKUP_LEVERS for record in items)


def checked(stored: Stored, items: Sequence[SettingsSet]) -> None:
    """Refuses the items that name a variant the table of what is allowed does not let into its queue, and a compilation
    that is no better than the episode's own video; nothing is returned for the items that pass."""
    for record in items:
        if _picks(record):
            _require_pickable(stored, record)
        if _takes_compilation(record) and not compilations.fits(
                stored.rows, settings.canonical(stored.records), record.season, record.episode, record.value,
                source_order(stored)):
            raise ValueError(reason("compilation_not_better"))


def writes_of(stored: Stored, items: Sequence[SettingsSet], voices: VoiceSettingsView) -> list[SettingRecord]:
    """What the items write, in order. The first edit of the markup reference on a level that follows the reference
    makes its queue its own (stage 14): the queue of the reference as the level shows it — its order, the variants it
    leaves out and the groups of equal variants — is written before the edit, so that from then on the two queues are
    apart. Each item is read against the records the items before it left."""
    if not any(record.lever in MARKUP_LEVERS for record in items):
        return list(items)
    records = settings.canonical(stored.records)
    writes: list[SettingRecord] = []
    for record in items:
        if record.lever in MARKUP_LEVERS:
            for copy in markup_copy(stored, records, record, voices):
                records = settings.apply_set(records, copy)
                writes.append(copy)
        records = settings.apply_set(records, record)
        writes.append(record)
    return writes


def markup_copy(stored: Stored, records: Sequence[SettingRecord], record: SettingsSet, voices: VoiceSettingsView
                ) -> list[SettingRecord]:
    """The records that make the queue of the markup reference of a level its own, when it still follows the reference."""
    lines, levers, order, _ = queues.level_of(stored, records, record, voices)
    if not queues.follows_reference(levers):
        return []
    shown, _ = queues.markup(lines, levers, order, record.episode)
    scope = dict(series_id=record.series_id, season=record.season, episode=record.episode,
                 compilation=record.compilation)
    copied = [SettingRecord(**scope, lever=MARKUP_ORDER, value=[
        {"group": row.group, "source_id": row.source_id, "player": row.player} for row in shown.rows])]
    copied += [SettingRecord(**scope, lever=MARKUP_ALLOW, target=variant_target(row.group, row.source_id, row.player),
                             value=False) for row in shown.rows if not row.allowed]
    # The variants the reference's queue shows as equal are equal in the queue that is made its own.
    same = queues.same_groups(shown.rows)
    if same:
        copied.append(SettingRecord(**scope, lever=MARKUP_SAME, value=same))
    return copied


def with_writes(stored: Stored, writes: Sequence[SettingRecord]) -> Edit:
    """The records after the writes of one command, in order."""
    before = settings.canonical(stored.records)
    after = before
    for each in writes:
        after = settings.apply_set(after, each)
    return _effect(stored, before, after)


def with_reset(stored: Stored, scope: SettingsReset) -> Edit:
    """The records after a level, or one lever of it, goes back to the level above."""
    before = settings.canonical(stored.records)
    return _effect(stored, before, settings.apply_reset(before, scope))


def _effect(stored: Stored, before: list[SettingRecord], after: list[SettingRecord]) -> Edit:
    """What a change of the records does to the episodes. A header that no episode follows still changes on the screen: that
    is told to windows, not to consumers."""
    if after == before:
        return Edit(before, False, set(), False)
    episodes = project.layout_episodes(stored.rows)
    order = source_order(stored)
    was, now = shape(stored.rows, before, episodes, order), shape(stored.rows, after, episodes, order)
    touched = settings.affected(was[0], now[0])
    return Edit(after, True, touched, bool(touched) or was[1] != now[1])


def reordered(stored: Stored, command: VoiceOrderSet) -> Reordered | None:
    """The order of the voice-overs of a season the user wants; None when it is the one in force."""
    orders = project.season_orders(stored.orders, stored.rows, {})
    current = orders.get(command.season, [])
    wanted = project.reorder(current, command.labels)
    if wanted == current and orders == stored.orders:
        return None
    orders[command.season] = wanted
    return Reordered(orders, {(command.season, number) for season, number in project.layout_episodes(stored.rows)
                              if season == command.season})


def renamed(stored: Stored, command: VoiceTitleSet) -> Renamed | None:
    """The names of the tracks the user wants; None when they are the ones in force."""
    titles = dict(stored.titles)
    if command.title and command.title != command.label:
        titles[command.label] = command.title
    else:
        titles.pop(command.label, None)
    if titles == stored.titles:
        return None
    return Renamed(titles, set(project.layout_episodes(stored.rows)))


def _picks(record: SettingRecord) -> bool:
    return isinstance(record.value, list) and bool(record.value) and record.lever in _PICKED


def _takes_compilation(record: SettingRecord) -> bool:
    return record.lever == COMPILATION_MEMBER and bool(record.value)


def _require_pickable(stored: Stored, record: SettingRecord) -> None:
    """A variant is put into the order of the reference, or of a group, only where the table of what is allowed lets
    it: the same rule the window lists the variants of a queue by."""
    single = record.lever in _SINGLES
    action = availability.Action.CHOOSE_REFERENCE if single else availability.Action.PREFER_VARIANT
    # The state is that of the level the setting is for, as the window shows it: the lines of that season or episode.
    lines = availability.lines_of_level(stored.rows, record.season, record.episode)
    # The groups of equal variants are lists of variants: each variant of them is put to the same test as one of an order.
    items = [item for group in record.value for item in group] if record.lever in _SETS else record.value
    for item in items:
        group = item.get("group") if single else parse_group_target(record.target or "")
        found = [row for row in lines if row.voice_label == group and row.source_id == item.get("source_id")
                 and row.player == item.get("player")]
        availability.require(action, availability.variant_state(found))
