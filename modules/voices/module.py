"""Module voices: the settings of the voice-overs (mission voice-alignment).

The work with the voice-overs of a series — their downloads, their alignment, the tracks of the containers — is the plan of
the series (modules/planner, stage 12). What is left here is what the user sets once for every series: the groups of the
names of voice-overs, the order of the players, the groups of players.

Settings: voices.source_priority (JSON list of player kinds), voices.players_off (JSON list of the players of the order
switched off for every source), voices.groups and voices.player_groups (JSON objects
{group: [names]}: a voice-over, or a player, called by one of the names belongs to the group). They are read together
(calc/voices/settings.py): a wrong value is logged once and its default is used.

Queries (the answer is the model, as it is: nobody changes it):
  voices.groups.resolve GroupsResolveRequest -> GroupsResolveReply: the user group of every name (None for a name no group
      holds), by the folded name or trigger of the group (calc/voice_names.py); asked by `composition` for the names of the
      voice-overs of its rows. A combined credit "A / B" is one name.
  voices.settings.get {values?} -> VoiceSettingsView: the groups of voice-overs and the order of sources as they apply (the
      order the user saved, then every other player the sources know, in their order: `sources.players.list`), the
      order to return to and the fields whose stored value was refused; with `values` (RunSnapshot.voices), what applied
      when a run took them.
  voices.settings.export {} -> {values}: the stored settings as they are, for the snapshot of a run.
  voices.players.get {} -> PlayersListReply: every player of the order with where it is among the sources of the series
      (the sources of every series of `catalog.series.list`, asked of `sources.players.list`; the torrent is one player of
      the order, its releases one place); for the screen of the order, not for a count.
Command:
  voices.settings.set VoiceSettingsDraft -> VoiceSettingsSaved: groups, their languages and order are checked
      (calc/voices/editor.py check: a known language or none; a player must be one the sources know unless it is stored
      already) and written in one step through settings.values.set_many, or refused with coded problems and nothing
      written; the next read of the settings uses the new values, no restart. Only the keys the form changes
      (editor.changed) are checked and written: the groups are saved whatever the order of sources holds (a provider that
      is not up leaves its lines unknown), and the order whatever the groups hold.
Events out:
  voices.settings.changed VoiceSettingsView — the settings as they apply after a save, so that every open settings screen
      shows them without asking back.

The counts are those of `calc/voices`, made here without the pool: a parse of the stored values, the view of the screen and
the group of a name are microseconds each (on 2026-11-21: 80–140 µs on the stored settings of the service), less than a hop
to the pool, and a request for the settings must not queue behind the count of a page in it (the planner and the layout
ask for them at every recount).
"""
from __future__ import annotations

from calc.voices import editor
from calc.voices.reply import groups_reply, players_request, save_of
from calc.voices.settings import PREFIX, VoiceSettings, parse
from core.contracts import (GroupsResolveReply, GroupsResolveRequest, PlayerFacts, PlayersListReply, VoiceSettingsDraft,
                            VoiceSettingsSaved, VoiceSettingsView)
from core.envelope import Envelope
from core.module import BaseModule
from core.topics import (CATALOG_SERIES_LIST, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SOURCES_PLAYERS_LIST,
                         VOICES_GROUPS_RESOLVE, VOICES_PLAYERS_GET, VOICES_SETTINGS_CHANGED, VOICES_SETTINGS_EXPORT,
                         VOICES_SETTINGS_GET, VOICES_SETTINGS_SET)


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db=None) -> None:
        self._reported: set = set()
        super().__init__(bus)

    def register(self) -> None:
        self.handle(VOICES_GROUPS_RESOLVE, self.on_groups_resolve)
        self.handle(VOICES_SETTINGS_GET, self.on_settings_get)
        self.handle(VOICES_SETTINGS_EXPORT, self.on_settings_export)
        self.handle(VOICES_PLAYERS_GET, self.on_players_get)
        self.handle(VOICES_SETTINGS_SET, self.on_settings_set)

    async def _settings(self, values: dict | None = None) -> VoiceSettings:
        """Every setting of the module in one read, or the values a run took (RunSnapshot.voices); a problem with a value
        is logged once, then the default is used."""
        if values is None:
            values = (await self.request(SETTINGS_VALUES_BY_PREFIX, {"prefix": PREFIX})).get("values") or {}
        settings = parse(values)
        for problem in sorted(set(settings.problems) - self._reported):
            self.log.warning("озвучки: настройка: %s", problem)
        self._reported = set(settings.problems)
        return settings

    async def _players(self) -> tuple[PlayerFacts, ...]:
        """Every player a row can be of, as the sources name them; none when the sources do not answer (the order then
        holds the players it names itself)."""
        try:
            return (await self.reply_of(SOURCES_PLAYERS_LIST, {})).players
        except Exception as exc:  # noqa: BLE001 - the order of sources is shown without the players it cannot learn
            self.log.warning("озвучки: список плееров не получен: %s", exc)
            return ()

    async def on_groups_resolve(self, env: Envelope) -> GroupsResolveReply:
        request = GroupsResolveRequest.model_validate(env.payload)
        return groups_reply(await self._settings(request.values), request)

    async def on_settings_get(self, env: Envelope) -> VoiceSettingsView:
        settings = await self._settings((env.payload or {}).get("values"))
        return editor.view(settings, await self._players())

    async def on_players_get(self, env: Envelope) -> PlayersListReply:
        series = await self.request(CATALOG_SERIES_LIST, {})
        return await self.reply_of(SOURCES_PLAYERS_LIST, players_request(series).model_dump(mode="json"))

    async def on_settings_export(self, env: Envelope) -> dict:
        reply = await self.request(SETTINGS_VALUES_BY_PREFIX, {"prefix": PREFIX})
        return {"values": reply.get("values") or {}}

    async def on_settings_set(self, env: Envelope) -> VoiceSettingsSaved:
        draft = VoiceSettingsDraft.model_validate(env.payload)
        current = await self._settings()
        players = await self._players()
        save = save_of(draft, current, players)
        if save.values:
            await self.request(SETTINGS_VALUES_SET_MANY, {"values": save.values})
        view = editor.view(await self._settings(), players)
        if save.values:
            # The event recounts the layout of every series: a save that wrote nothing changed nothing to recount.
            self.log.info("озвучки: настройки сохранены")
            self.publish_event(VOICES_SETTINGS_CHANGED, view.model_dump(mode="json"))
        return VoiceSettingsSaved(saved=not save.problems, problems=save.problems, settings=view)
