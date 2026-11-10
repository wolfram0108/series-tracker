"""The lines of a video application that are players: those admitted by downloading whole the first, the central and the
last episode of the line and reading each file back (tools/collectors/app_lines_admission.py), as the registry of the
application records it (`admitted/<application>.json`, made by tools/analyzers/app_lines_registry.py).

A line the registry does not admit is no player: its episodes are no rows of a table and it has no place in the order of
sources; the registry says why a refused line was refused, and a line it does not know was never admitted.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

ADMITTED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admitted")


@dataclass(frozen=True)
class LineRegistry:
    """The admitted lines of one application, by code, and the reason of each refused one."""
    admitted: frozenset[str] = frozenset()
    refused: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, app: str, folder: str | None = None) -> "LineRegistry":
        """The registry of an application; none admitted when it has no registry."""
        path = os.path.join(folder or ADMITTED_DIR, f"{app}.json")
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as stored:
            data = json.load(stored)
        refused = {code: f"{each.get('episode')}: {each.get('step')} — {each.get('reason')}"
                   for code, each in (data.get("refused") or {}).items()}
        return cls(frozenset(data.get("admitted") or {}), refused)

    def admits(self, code: str) -> bool:
        return code in self.admitted

    def why_not(self, code: str) -> str:
        return self.refused.get(code) or "not admitted: no download of its first, central and last episode"
