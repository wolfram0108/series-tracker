"""Why the video of every episode is the one the plan takes: the reference as the window reads it (core/contracts/reference),
told by the plan itself, so that what the window shows and what the sync makes are one choice.

  off           the user switched the episode off: its file is hidden, it takes nothing new;
  none          no row of the episode can give its video;
  unfit         no single video, only a compilation that covers the episode;
  other_player  the variant the user put first has no row of the episode, or its download failed: the next one is taken;
  own / by_level the plan's choice by the levers of the episode itself, or of the levels above it."""
from __future__ import annotations

from typing import Mapping

from core import native
from core.contracts import CompilationRef, Reference

from .planner import Plan


def references_of(made: Plan, members: Mapping[tuple[int, str], CompilationRef] | None = None) -> list[Reference]:
    """One reference for every place the layout or the asset has, in the order of season, episode, part, counted by the core
    (native/src/plan_view.rs) from the goals of the plan. `members` are the compilations of the layout by their row (source
    id, row key), for the episode whose video is cut out of one."""
    return [Reference.model_validate(each) for each in native.plan_references(
        made.counted, {key: each.model_dump(mode="json") for key, each in (members or {}).items()})]
