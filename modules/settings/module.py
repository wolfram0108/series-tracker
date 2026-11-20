"""Module settings: owner of the table settings (key-value) and of the saved paths.

Other modules get the settings by a query (Р-7: the data of another module only through the bus) and may react to the event
settings.changed. Nothing here is a count: the checks are of shape (the models) and of the secrets, so no pure step is
taken out to `calc`; a secret is the one thing the module must know (`core/secrets.py`).

Queries (the answer is the model, as it is):
  settings.value.get {key} -> SettingValue: the value of a key, none when it is not set; a secret is read decrypted.
  settings.values.by_prefix {prefix} -> SettingValues: the values of the keys that begin with the prefix, for example
      debug_enabled_* (Р-22); a secret is read by its key alone, never in bulk.
  settings.paths.list {} -> SavedPaths

Commands that answer:
  settings.value.set {key, value} -> SettingValue: the value written; the event settings.changed follows.
  settings.values.set_many {values: {key: value}} -> SettingValues: every key written in one transaction (a form is saved
      whole or not at all); settings.changed follows for every key.
  settings.paths.add {path} -> SavedPaths: the path made safe for the disk (an empty one is not saved); the list after.
  settings.paths.remove {id} -> SavedPaths: the list after.

Events out:
  settings.changed SettingValue: the key and its value, as a JSON packet; the event goes round the bus and into the dumps of the
      runs, so a secret is told as changed, never as its value (an empty one).
"""
from __future__ import annotations

from core.contracts import SavedPaths, SettingValue, SettingValues
from core.db import Database
from core.envelope import Envelope
from core.fsnames import sanitize_path
from core.module import BaseModule
from core.topics import (SETTINGS_CHANGED, SETTINGS_PATHS_ADD, SETTINGS_PATHS_LIST, SETTINGS_PATHS_REMOVE,
                         SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY)

from .repository import SECRET_KEYS, SettingsRepository


class SettingsModule(BaseModule):
    name = "settings"

    def __init__(self, bus, db: Database) -> None:
        self.repo = SettingsRepository(db)
        super().__init__(bus)

    def register(self) -> None:
        self.handle(SETTINGS_VALUE_GET, self.on_get)
        self.handle(SETTINGS_VALUE_SET, self.on_set)
        self.handle(SETTINGS_VALUES_BY_PREFIX, self.on_by_prefix)
        self.handle(SETTINGS_VALUES_SET_MANY, self.on_set_many)
        self.handle(SETTINGS_PATHS_LIST, self.on_paths_list)
        self.handle(SETTINGS_PATHS_ADD, self.on_paths_add)
        self.handle(SETTINGS_PATHS_REMOVE, self.on_paths_remove)

    async def on_start(self) -> None:
        # Stage 3Б: the secret keys (tmdb_token) are encrypted once.
        migrated = await self.repo.encrypt_legacy_secrets()
        if migrated:
            self.log.info("зашифровано legacy-секретов settings: %d", migrated)

    async def on_get(self, env: Envelope) -> SettingValue:
        key = env.payload["key"]
        return SettingValue(key=key, value=await self.repo.get(key))

    async def on_set(self, env: Envelope) -> SettingValue:
        # The shape is checked before the write: a value that is no text is refused, not stored half-way.
        written = SettingValue(key=env.payload["key"], value=env.payload["value"])
        await self.repo.set(written.key, written.value)
        self._changed(written)
        return written

    async def on_set_many(self, env: Envelope) -> SettingValues:
        """{values: {key: value}} written in one transaction; settings.changed follows for every key."""
        written = SettingValues(values={str(key): str(value) for key, value in env.payload["values"].items()})
        await self.repo.set_many(written.values)
        for key, value in written.values.items():
            self._changed(SettingValue(key=key, value=value))
        return written

    def _changed(self, setting: SettingValue) -> None:
        """The event goes round the bus and into the dumps of the runs: a secret is told as changed, never as its value."""
        told = SettingValue(key=setting.key, value="" if setting.key in SECRET_KEYS else setting.value)
        self.publish_event(SETTINGS_CHANGED, told.model_dump(mode="json"))

    async def on_by_prefix(self, env: Envelope) -> SettingValues:
        return SettingValues(values=await self.repo.by_prefix(env.payload["prefix"]))

    # --- saved_paths -------------------------------------------------------

    async def on_paths_list(self, env: Envelope) -> SavedPaths:
        return SavedPaths(paths=await self.repo.list_paths())

    async def on_paths_add(self, env: Envelope) -> SavedPaths:
        path = sanitize_path(env.payload.get("path") or "")
        if path:
            await self.repo.add_path(path)
        return SavedPaths(paths=await self.repo.list_paths())

    async def on_paths_remove(self, env: Envelope) -> SavedPaths:
        await self.repo.remove_path(int(env.payload["id"]))
        return SavedPaths(paths=await self.repo.list_paths())
