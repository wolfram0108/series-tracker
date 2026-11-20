"""The view of a series as the window is told it, counted from what the tables and the plan give: the asset, the plan of every
episode, the failures, the commands, the problems of the last sync and the reference of every episode; and what the series stands
at by it. Pure work over thousands of rows: the module of the planner counts it in the pool."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from calc.plan.marks import references_of
from calc.plan.planner import Plan
from calc.plan.tables import Container, Inputs
from calc.view.view import view_of
from core.contracts import CompilationRef, PlannerView


@dataclass(frozen=True)
class Held:
    """What the view reads of a sync: its table, its failures and its commands at one moment."""

    containers: Mapping[str, Container]
    failed: frozenset
    commands: frozenset


def view_body(series_id: int, held: Held, made: Plan | None, inputs: Inputs | None,
              members: Mapping[tuple[int, str], CompilationRef], running: bool, problems: list) -> PlannerView:
    """The view of the window from what the tables and the plan give."""
    # Online is what the sources give now: a row that stands from an earlier read is not.
    online = frozenset(row.id for row in inputs.online if row.current) if inputs is not None else None
    references = references_of(inputs, made, members) if inputs is not None and made is not None else []
    view = {**view_of(series_id, held, made, running=running, online=online,
                      rows=inputs.online if inputs is not None else ()),
            "problems": problems, "references": references}
    return PlannerView.model_validate(view)


def packet_of(view: PlannerView) -> dict:
    """The view as the event `planner.series.changed` carries it and the window reads it."""
    return view.model_dump(mode="json")


def standing_of(view: PlannerView) -> dict[str, bool]:
    """What the series stands at, told by its view: ready — a file of it is in the library; waiting — the plan has work for the
    next sync; error — an episode lacks what a recorded attempt was for (`covered` of the view: another source that gave it
    leaves no error), or work of the last sync failed for the moment and nothing gave it."""
    work = any(goal.video.kind != "asset" or any(track.kind != "reuse" for track in goal.tracks)
               for goal in view.episodes) or bool(view.commands)
    return {"ready": bool(view.assets), "waiting": work and not view.running,
            "error": any(not each.covered for each in view.failures) or bool(view.problems)}
