"""Why the video of every episode is the one the plan takes: the reference as the window reads it (core/contracts/reference),
told by the plan itself, so that what the window shows and what the sync makes are one choice.

  off           the user switched the episode off: its file is hidden, it takes nothing new;
  none          no row of the episode can give its video;
  unfit         no single video, only a compilation that covers the episode;
  other_player  the variant the user put first has no row of the episode, or its download failed: the next one is taken;
  own / by_level the plan's choice by the levers of the episode itself, or of the levels above it."""
from __future__ import annotations

from typing import Mapping

from core.contracts import CompilationRef, CutPicture, Reference, ReferenceMark
from core.reasons import reason

from .planner import Plan
from .tables import Inputs, OnlineRow, Slot, episode_of


def _part(slot: Slot) -> int | None:
    return slot[2] if len(slot) > 2 else None


def references_of(inputs: Inputs, made: Plan,
                  members: Mapping[tuple[int, str], CompilationRef] | None = None) -> list[Reference]:
    """One reference for every place the layout or the asset has, in the order of season, episode, part. `members` are the
    compilations of the layout by their row (source id, row key), for the episode whose video is cut out of one."""
    members = members or {}
    choices = inputs.choices
    online = {row.id: row for row in inputs.online}
    singles: dict[Slot, list[OnlineRow]] = {}
    covered: set[tuple[int, int]] = set()
    for row in inputs.online:
        if row.compilation:
            if row.playable:
                covered |= {(row.season, number) for number in range(row.start, row.end + 1)}
        else:
            singles.setdefault(row.slot, []).append(row)
    goals = {goal.slot: goal for goal in made.goals}
    found: list[Reference] = []
    for slot in sorted(set(singles) | set(goals) | {(season, episode) for season, episode in covered
                                                     if not any(key[:2] == (season, episode) for key in singles)}):
        episode = episode_of(slot)
        goal = goals.get(slot)
        if goal is None:
            mark = ReferenceMark.OFF if episode in choices.off_episodes else \
                ReferenceMark.UNFIT if episode in covered else ReferenceMark.NONE
            why = None if mark is ReferenceMark.OFF else reason("reference_unfit" if mark is ReferenceMark.UNFIT
                                                                else "reference_none")
            found.append(Reference(season=slot[0], episode=slot[1], part=_part(slot), mark=mark, reason=why))
            continue
        video = goal.video
        single = video.ref2 if video.kind == "cut" else video
        row = online.get(single.timeline)
        snapshot = single.snapshot
        variant = (snapshot.variant_label, single.timeline.source_id, snapshot.player)
        mark, why = (ReferenceMark.OWN if episode in choices.own_reference else ReferenceMark.BY_LEVEL), None
        denied = choices.reference_deny.get(episode, frozenset())
        wanted = next((each for each in choices.reference.get(episode, ()) if each not in denied), None)
        lost = [each for each in singles.get(slot, ()) if ("download", each.id) in inputs.failed]
        if goal.off:
            mark = ReferenceMark.OFF
        elif lost and all(each.id != single.timeline for each in lost):
            head = lost[0]
            mark, why = ReferenceMark.OTHER_PLAYER, reason(
                "reference_download_failed", taken=snapshot.player, failed=head.player,
                taken_source=single.timeline.source_id, failed_source=head.id.source_id)
        elif wanted is not None and variant != wanted:
            mark, why = ReferenceMark.OTHER_PLAYER, reason(
                "reference_other_player", taken=snapshot.player, missing=wanted[2],
                taken_source=single.timeline.source_id, missing_source=wanted[1])
        cut_of = video.row.id if video.kind == "cut" else video.container.video.cut_of if video.container is not None else None
        # The picture of a cut video: the row it is cut out of, as the plan has it now or as its file was made.
        picture = None
        if cut_of is not None:
            shot = video.snapshot if video.kind == "cut" else video.container.video.snapshot
            picture = CutPicture(source_id=cut_of.source_id, player=shot.player, height=shot.height,
                                 voice_group=shot.variant_label or None)
        # The reference is the single video (the timeline a cut is made on): its row as the layout has it now, or, for a
        # row the source forgot, the snapshot of its file.
        found.append(Reference(
            season=slot[0], episode=slot[1], part=_part(slot), mark=mark, source_id=single.timeline.source_id,
            row_key=single.timeline.row_key, player=row.player if row is not None else snapshot.player,
            voice_group=(row.label or row.group) if row is not None else (snapshot.variant_label or None),
            height=row.height if row is not None else snapshot.height, reason=why,
            compilation=members.get((cut_of.source_id, cut_of.row_key)) if cut_of is not None else None,
            picture=picture, markup=goal.markup is not None and single.timeline == goal.markup.id))
    return found
