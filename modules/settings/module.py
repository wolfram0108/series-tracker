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
        self.handle("settings.values.by_prefix", self.on_by_prefix)
        self.handle("settings.paths.list", self.on_paths_list)
        self.handle("settings.paths.add", self.on_paths_add)
        self.handle("settings.paths.remove", self.on_paths_remove)

    async def on_get(self, env: Envelope) -> dict:
        key = env.payload["key"]
        return {"key": key, "value": await self.repo.get(key)}

    async def on_set(self, env: Envelope) -> dict:
        key, value = env.payload["key"], env.payload["value"]
        await self.repo.set(key, value)
        self.publish_event("settings.changed", {"key": key, "value": value})
        return {"key": key, "value": value}

    async def on_by_prefix(self, env: Envelope) -> dict:
        """{values: {key: value}} — например, debug_enabled_* (Р-22)."""
        return {"values": await self.repo.by_prefix(env.payload["prefix"])}

    # --- saved_paths -------------------------------------------------------

    async def on_paths_list(self, env: Envelope) -> dict:
        return {"paths": await self.repo.list_paths()}

    async def on_paths_add(self, env: Envelope) -> dict:
        path = (env.payload.get("path") or "").strip()
        if path:
            await self.repo.add_path(path)
        return {"paths": await self.repo.list_paths()}

    async def on_paths_remove(self, env: Envelope) -> dict:
        await self.repo.remove_path(int(env.payload["id"]))
        return {"paths": await self.repo.list_paths()}
