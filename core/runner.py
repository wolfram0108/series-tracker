"""Раннер: сборка и жизненный цикл набора модулей над одной шиной."""
from __future__ import annotations

from .bus import Bus
from .logging import get_logger
from .module import BaseModule


class Runner:
    def __init__(self, bus: Bus, modules: list[BaseModule]) -> None:
        self.bus = bus
        self.modules = modules
        self.log = get_logger("runner")

    async def start(self) -> None:
        for module in self.modules:
            await module.start()
        self.log.info("запущено модулей: %d (%s)", len(self.modules),
                      ", ".join(m.name for m in self.modules))

    async def stop(self) -> None:
        # Остановка в обратном порядке: кто стартовал последним, гасится первым.
        for module in reversed(self.modules):
            await module.stop()
        self.log.info("все модули остановлены")
