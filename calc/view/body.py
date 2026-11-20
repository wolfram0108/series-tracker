"""The view of a series as the window is told it, counted from what the tables and the plan give: the asset, the plan of every
episode, the failures, the commands, the problems of the last sync and the reference of every episode. Pure work over thousands
of rows: the module of the planner counts it in the pool."""
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
              members: Mapping[tuple[int, str], CompilationRef], running: bool, problems: list) -> dict:
    """The view of the window from what the tables and the plan give."""
    # Online is what the sources give now: a row that stands from an earlier read is not.
    online = frozenset(row.id for row in inputs.online if row.current) if inputs is not None else None
    references = references_of(inputs, made, members) if inputs is not None and made is not None else []
    view = {**view_of(series_id, held, made, running=running, online=online,
                      rows=inputs.online if inputs is not None else ()),
            "problems": problems, "references": [each.model_dump(mode="json") for each in references]}
    return PlannerView.model_validate(view).model_dump(mode="json")
