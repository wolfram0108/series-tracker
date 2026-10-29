"""Obligations that must be carried to the end and whose truth is in the database.

A step that failed for a reason that may pass (the service does not answer, the share is stalled) is not left
for some later event to notice: it keeps its own account of attempts and is tried again after a pause that
doubles with every failure up to a ceiling. After a set number of failures the owner is told once and may end
the obligation; one that is not ended goes on at the longest pause. An event that says the cause is gone
(`poke`) only brings the next attempt forward.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

Attempt = Callable[[], Awaitable[bool]]  # True: the obligation is over (also when nothing is left to do)
Report = Callable[[], Awaitable[bool]]  # True: stop trying


@dataclass
class _Item:
    attempt: Attempt
    report: Report | None
    failures: int = 0
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


class Recovery:
    def __init__(self, log: logging.Logger, pause: Callable[[int], float], report_after: Callable[[], int]) -> None:
        self._log = log
        self._pause = pause
        self._report_after = report_after
        self._items: dict[str, _Item] = {}

    def want(self, key: str, attempt: Attempt, report: Report | None = None, *, now: bool = False) -> None:
        """Carry `key` to the end; asking again for a key that is being carried changes nothing."""
        if key in self._items:
            return
        item = self._items[key] = _Item(attempt, report)
        item.task = asyncio.create_task(self._carry(key, item, now), name=f"voices:recovery:{key}")

    def poke(self, prefix: str = "") -> None:
        """The cause of a failure may be gone: the obligations under `prefix` try again now."""
        for key, item in self._items.items():
            if key.startswith(prefix):
                item.wake.set()

    async def stop(self) -> None:
        tasks = [item.task for item in self._items.values() if item.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def failures(self) -> dict[str, int]:
        """What is still being carried and how many attempts of each have failed."""
        return {key: item.failures for key, item in self._items.items()}

    async def _carry(self, key: str, item: _Item, now: bool) -> None:
        try:
            if not now:
                await self._sleep(item)
            while True:
                try:
                    if await item.attempt():
                        return
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - an obligation survives the failure of one attempt
                    self._log.exception("восстановление %s: попытка не удалась", key)
                item.failures += 1
                if item.failures == self._report_after():
                    self._log.error("восстановление %s: %d попыток без результата, продолжаем с паузой %.0f с", key,
                                    item.failures, self._pause(item.failures))
                    if item.report is not None and await self._ended_by_owner(key, item):
                        return
                await self._sleep(item)
        finally:
            if self._items.get(key) is item:
                del self._items[key]

    async def _ended_by_owner(self, key: str, item: _Item) -> bool:
        try:
            return bool(await item.report())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._log.exception("восстановление %s: сообщить владельцу не удалось", key)
            return False

    async def _sleep(self, item: _Item) -> None:
        item.wake.clear()
        try:
            await asyncio.wait_for(item.wake.wait(), self._pause(item.failures))
        except asyncio.TimeoutError:
            pass
