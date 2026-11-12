"""The lines of a video application that are players: those admitted by downloading whole the first, the central and the
last episode of the line and reading each file back (tools/collectors/app_lines_admission.py), as the registry of the
application records it (`admitted/<application>.json`, made by tools/analyzers/app_lines_registry.py).

A line the registry does not admit is no player: its episodes are no rows of a table and it has no place in the order of
sources; the registry says why a refused line was refused, and a line it does not know was never admitted.

A line the owner tested himself and stated is a player as well (`admitted/confirmed.json`, the lines by application with
the day of the statement): the statement is the owner's, it is kept apart from the records of the admission so that making
a registry from them does not lose it, and the table of such a line still gives every row the frame of its own episode.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

ADMITTED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admitted")
CONFIRMED_FILE = "confirmed.json"


@dataclass(frozen=True)
class LineRegistry:
    """The admitted lines of one application, by code, and the reason of each refused one."""
    admitted: frozenset[str] = frozenset()
    refused: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, app: str, folder: str | None = None) -> "LineRegistry":
        """The registry of an application; none admitted when it has no registry. The lines the owner stated are admitted
        and no longer refused."""
        folder = folder or ADMITTED_DIR
        path, stated = os.path.join(folder, f"{app}.json"), os.path.join(folder, CONFIRMED_FILE)
        data, confirmed = {}, frozenset()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as stored:
                data = json.load(stored)
        if os.path.exists(stated):
            with open(stated, encoding="utf-8") as stored:
                confirmed = frozenset((json.load(stored).get("lines") or {}).get(app) or ())
        refused = {code: f"{each.get('episode')}: {each.get('step')} — {each.get('reason')}"
                   for code, each in (data.get("refused") or {}).items() if code not in confirmed}
        return cls(frozenset(data.get("admitted") or {}) | confirmed, refused)

    def admits(self, code: str) -> bool:
        return code in self.admitted

    def why_not(self, code: str) -> str:
        return self.refused.get(code) or "not admitted: no download of its first, central and last episode"


ROUTES_FILE = os.path.join(os.path.dirname(ADMITTED_DIR), "routes.json")


def routes_of(app: str, path: str | None = None) -> dict[str, tuple[str, str]]:
    """The lines of an application that another application parses when its own parser says no: line -> (the application,
    its line). Both are the same archive under one code: the token of an episode of the line is the archive's own, so the
    parser of the other application, which the archive publishes as well, takes it as it is (`routes.json`; the other
    application's profile must be in the environment, else the line has no route)."""
    path = path or ROUTES_FILE
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as stored:
        data = (json.load(stored).get(app) or {})
    return {line: (each["through"], each.get("line") or line) for line, each in data.items()}
