"""What the window shows of the plan of a series (PlannerView): the asset (every file with the provenance of its video and
tracks, and whether its row is online), the goal of every episode as the last plan counted it, the reference of every
episode and why it is that one (core/plan/marks), the failures and the commands — counted by the core (core.native,
native/src/plan_view.rs) from the goals it counted."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import orjson

from core import native
from core.contracts import PlanProblem
from core.plan.executor import SeriesSync
from core.plan.marks import members_text
from core.plan.planner import Plan
from core.plan.tables import OnlineRow


def shown_of(made: Plan | None) -> tuple | None:
    """What the view reads of a plan: the goals the core counted and the name of each."""
    return None if made is None else (made.counted, [goal.name for goal in made.goals])


def covered(failure: dict, assets: list[dict]) -> bool:
    """A failed attempt the series does not lack: every episode of its row has a file in the library that holds what the
    attempt was for — its voice-over, as a track or as the own sound of the video (another source gave it), or the video
    when the row names no voice-over. A row no longer online names no episode and leaves nothing lacking."""
    return native.plan_covered(failure, assets)


def placed(failed: Iterable[tuple], rows: Iterable[OnlineRow] = ()) -> list[dict]:
    """The failed attempts (`key`) with where each belongs, as the layout the rows are of has it: the episodes its row gives
    and its voice-over group; empty when the row is offline."""
    return native.plan_placed(list(failed), list(rows))


@dataclass(frozen=True)
class CountedView:
    """A view counted: the view and its references as texts of JSON (native.JsonText: what the gateway writes out as they
    are, read here as the dict and the list they are), and what the card of the series is told of it."""

    view: Any
    references: Any
    ready: bool  # a file of the series is in the library
    work: bool  # the plan has work for a sync, or a command waits
    error: bool  # an episode lacks what a recorded attempt was for, or a step of the last sync failed for the moment
    running: bool


def counted_view(series_id: int, sync: SeriesSync, shown: tuple | None, *, running: bool,
                 members: Mapping | None = None, problems: Iterable[dict] = ()) -> CountedView:
    """The view, as `PlannerView.model_dump(mode="json")` gives it, counted by the core. `shown` is the goals counted
    (`planner.count`) and the name of each (`shown_of` of a plan), none when no plan was counted; `members` the
    compilations of the layout by their row (source id, row key)."""
    counted, names = shown if shown is not None else (None, None)
    view, references, (ready, work, error) = native.plan_view(
        series_id, running, list(sync.containers.values()), sync.failed, sync.commands,
        orjson.dumps([PlanProblem.model_validate(each).model_dump(mode="json") for each in problems]), counted, names,
        members_text(members))
    return CountedView(view, references, ready, work, error, running)


def view_of(series_id: int, sync: SeriesSync, shown: tuple | None, *, running: bool,
            members: Mapping | None = None, problems: Iterable[dict] = ()):
    """The view alone (`counted_view`)."""
    return counted_view(series_id, sync, shown, running=running, members=members, problems=problems).view
