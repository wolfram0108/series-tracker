"""Модуль settings — владелец таблицы settings (ключ-значение).

Другие модули получают настройки query-запросом (Р-7: чужие данные —
только через шину) и могут реагировать на событие settings.changed.
"""
from __future__ import annotations

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from .repository import SettingsRepository


class SettingsModule(BaseModule):
    name = "settings"

    def __init__(self, bus, db: Database) -> None:
        self.repo = SettingsRepository(db)
        super().__init__(bus)

    def register(self) -> None:
        self.handle("settings.value.get", self.on_get)
        self.handle("settings.value.set", self.on_set)

    async def on_get(self, env: Envelope) -> dict:
        key = env.payload["key"]
        return {"key": key, "value": await self.repo.get(key)}

    async def on_set(self, env: Envelope) -> dict:
        key, value = env.payload["key"], env.payload["value"]
        await self.repo.set(key, value)
        self.publish_event("settings.changed", {"key": key, "value": value})
        return {"key": key, "value": value}
