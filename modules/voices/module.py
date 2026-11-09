"""Module voices: the settings of the voice-overs (mission voice-alignment).

The work with the voice-overs of a series — their downloads, their alignment, the tracks of the containers — is the plan of
the series (modules/planner, stage 12). What is left here is what the user sets once for every series: the groups of the
names of voice-overs, the order of the players, the groups of players.

Settings: voices.source_priority (JSON list of player kinds), voices.groups and voices.player_groups (JSON objects
{group: [names]}: a voice-over, or a player, called by one of the names belongs to the group). They are read together
(settings.py): a wrong value is logged once and its default is used.

Queries:
  voices.groups.resolve GroupsResolveRequest -> GroupsResolveReply: the user group of every name (None for a name no group
      holds), by the folded name or trigger of the group (core/voice_names.py); asked by `composition` for the names of the
      voice-overs of its rows. A combined credit "A / B" is one name.
  voices.settings.get {values?} -> VoiceSettingsView: the groups of voice-overs and the order of sources as they apply, the
      order to return to and the fields whose stored value was refused; with `values` (RunSnapshot.voices), what applied
      when a run took them.
  voices.settings.export {} -> {values}: the stored settings as they are, for the snapshot of a run.
Command:
  voices.settings.set VoiceSettingsDraft -> VoiceSettingsSaved: groups, their languages and order are checked
      (editor.check: a known language or none) and written in one step through settings.values.set_many, or
      refused with coded problems and nothing written; the next read of the settings uses the new values, no restart.
Events out:
  voices.settings.changed VoiceSettingsView — the settings as they apply after a save, so that every open settings screen
      shows them without asking back.
"""
from __future__ import annotations

from core import BaseModule
from core.contracts import GroupsResolveReply, GroupsResolveRequest, VoiceSettingsDraft, VoiceSettingsSaved
from core.envelope import Envelope

from . import editor
from .settings import PREFIX, VoiceSettings, parse


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db=None) -> None:
        self._reported: set = set()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("voices.groups.resolve", self.on_groups_resolve)
        self.handle("voices.settings.get", self.on_settings_get)
        self.handle("voices.settings.export", self.on_settings_export)
        self.handle("voices.settings.set", self.on_settings_set)

    async def _settings(self, values: dict | None = None) -> VoiceSettings:
        """Every setting of the module in one read, or the values a run took (RunSnapshot.voices); a problem with a value
        is logged once, then the default is used."""
        if values is None:
            values = (await self.request("settings.values.by_prefix", {"prefix": PREFIX})).get("values") or {}
        settings = parse(values)
        for problem in sorted(set(settings.problems) - self._reported):
            self.log.warning("озвучки: настройка: %s", problem)
        self._reported = set(settings.problems)
        return settings

    async def on_groups_resolve(self, env: Envelope) -> dict:
        request = GroupsResolveRequest.model_validate(env.payload)
        groups = (await self._settings(request.values)).voices
        return GroupsResolveReply(groups={name: groups.group_of(name) for name in request.names}).model_dump(mode="json")

    async def on_settings_get(self, env: Envelope) -> dict:
        return editor.view(await self._settings((env.payload or {}).get("values"))).model_dump(mode="json")

    async def on_settings_export(self, env: Envelope) -> dict:
        reply = await self.request("settings.values.by_prefix", {"prefix": PREFIX})
        return {"values": reply.get("values") or {}}

    async def on_settings_set(self, env: Envelope) -> dict:
        draft = VoiceSettingsDraft.model_validate(env.payload)
        current = await self._settings()
        problems = editor.check(draft, current.player_groups)
        if not problems:
            await self.request("settings.values.set_many", {"values": editor.stored(editor.clean(draft))})
        view = editor.view(await self._settings())
        if not problems:
            self.log.info("озвучки: настройки сохранены")
            self.publish_event("voices.settings.changed", view.model_dump(mode="json"))
        return VoiceSettingsSaved(saved=not problems, problems=problems, settings=view).model_dump(mode="json")
