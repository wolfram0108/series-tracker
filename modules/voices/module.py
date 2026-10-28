"""Module voices: the voice-over layout of a series (mission voice-alignment, stage 4).

No state yet: the layout is a pure function (layout.py).

Query:
  voices.layout {sources: [{url, player, studio, kind, episodes}], mode?, reference?}
      -> {mode, reference, voices: [{label, role, source, fallbacks}], skipped, priority}
      The source priority is the setting `voices.source_priority` (JSON list of player kinds);
      layout.DEFAULT_PRIORITY when unset.
"""
from __future__ import annotations

import json

from core import BaseModule
from core.envelope import Envelope

from .layout import DEFAULT_PRIORITY, plan_layout

PRIORITY_KEY = "voices.source_priority"


class VoicesModule(BaseModule):
    name = "voices"

    def register(self) -> None:
        self.handle("voices.layout", self.on_layout)

    async def _priority(self) -> list[str]:
        reply = await self.request("settings.value.get", {"key": PRIORITY_KEY}, timeout=10)
        try:
            value = json.loads(reply.get("value") or "null")
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, list) and all(isinstance(item, str) for item in value) and value:
            return value
        return list(DEFAULT_PRIORITY)

    async def on_layout(self, env: Envelope) -> dict:
        payload = env.payload
        return plan_layout(payload["sources"], payload.get("mode") or "manual",
                           payload.get("reference"), await self._priority())
