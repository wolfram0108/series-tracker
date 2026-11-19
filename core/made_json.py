"""A reply that is JSON already: made where its data lives (the native core, docs/missions/rust-core) and passed on as it
is — over the bus, through the gateway to a window — never parsed and written again on the way."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MadeJson:
    text: str

    def parsed(self) -> Any:
        """The value, for a reader in the process that needs it as data (a test, a tool): the work passes the text on."""
        return json.loads(self.text)
