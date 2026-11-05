"""What an executor (downloads, voices) tells the run of a series about its work of it (И-60).

The run is owned by scan; an executor does not know it. It tells whether it has live work of a series when that
changes (`work.series.state`) and answers `<owner>.work.get` with the same record. Every record is numbered (`seq`) in the
order its state was counted, one count at a time per series: a reply and an event of the same executor that cross on
the bus are put in order by the run, and a state counted earlier never overrides one counted later.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .contracts import WORK_STATE, WorkState


class WorkReport:
    def __init__(self, owner: str, has_work: Callable[[int], Awaitable[bool]],
                 publish: Callable[[str, dict], None]) -> None:
        self._owner, self._has_work, self.publish_event = owner, has_work, publish
        self._told: dict[int, WorkState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._seq = 0

    @property
    def series(self) -> set[int]:
        """The series this executor has told about."""
        return set(self._told)

    async def tell(self, series_id: int) -> WorkState:
        """Counts the work of the series now and tells it when it changed; the record in force is the answer."""
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            active = await self._has_work(series_id)
            told = self._told.get(series_id)
            if told is None or told.active != active:
                self._seq += 1
                told = WorkState(owner=self._owner, series_id=series_id, active=active, seq=self._seq)
                self._told[series_id] = told
                self.publish_event(WORK_STATE, told.model_dump())
            return told
