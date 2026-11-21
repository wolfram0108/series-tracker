"""Module rules — the owner of the profiles of rules and of the constructor of numbers (Р-8, stage 10).

The constructor is the only source of the number of an episode, a season, a range and a part (З-3): the layout sends it lines
with keys and gets the answer by key. A file name does not count numbers and does not apply the rules a second time. A profile
is assigned to a source of a series in catalog; the module reads no table of another module — the profile comes as a parameter
of the request, and where a profile is used is asked of catalog.

Queries:
  rules.apply {profile_id, lines: [{key, text}], series_season?}
      → {results: {key: {excluded, season, episode, start, end, part,
                         voiceover, events, errors, <the other extracted fields>}},
         invalid_rules: [...]}
      — the models RulesApplyRequest / RulesApplyReply; the answer goes as the model itself (immutable). series_season is the
      season of the card of the series, a fallback for when the rules extracted no season (calc/rules/season.py); an excluded
      line has no numbers. voiceover is the result of the action «assign a voice-over». Repeated keys in the request are a
      refusal (ValueError); an empty list of lines is an empty answer.
      Faulty rules (decision Б) are not applied, are returned in invalid_rules of every answer and are written to the log as an
      error.
  rules.format_filename {series, media_item, episode_override?, original_filename?}
      → {filename}
      — the name by the finished numbers of media_item (season, episode_start/end, part, resolution, voiceover_tag) and the card
      of the series; no profile is read. The extension is that of original_filename, `.mkv` without it.
  rules.format_torrent_file {series, profile_id, file_basename, original_path}
      → {filename, extracted}
      — the name of a torrent file with its season folder; the rules of the profile profile_id (required, ValueError without
      it) are applied to the file name. filename=None — no season can be told, the file is skipped.
  rules.profiles.list {kind?} → [profile] (kind: series — the default, or torrent — the profiles of the torrent editor)
  rules.profiles.export {profile_ids} → {profiles: {id: [rule]}} — the rules of the profiles as rules.apply takes them
  rules.rules.list {profile_id} → [rule]  (with the marks valid / error)
Commands (the answer is the model, as it is):
  rules.profiles.create {name, kind?} → Created {id}
  rules.profiles.update {profile_id, data} → Done
  rules.profiles.delete {profile_id} → Done
      — the profile and its rules go in one transaction; a profile assigned to sources (catalog.sources.by_profile) cannot be
      deleted: ValueError with the code `profile_in_use` (core/reasons). A source that took the profile between the question and
      the deletion is caught by the foreign key: the transaction rolls back, catalog is asked again and the refusal is the same;
      with no sources the database error stays as it is. Deleting a profile that is not there is no error: nothing changed, no
      event.
  rules.rules.add {profile_id, data} → Created {id}
  rules.rules.update {rule_id, data} → Done
  rules.rules.delete {rule_id} → Done
  rules.rules.reorder {ordered_ids} → Done
      — a rule of the kind «Таблица» carries `rule_table` {action_type, rows: [{if: blocks, then: value | blocks,
        action_type?}], language?}
        (`action_type` of a row is its own action instead of that of the column)
        (`language` is the default language of the rows of a catalog table that have none of their own)
      (null for an ordinary rule): the rows are the same rules «if it contains → the action of the column», the engine lays them
      out in order in the place of the table (engine.table_rules); a row without a condition or a value is a refusal naming its
      number.

Refusals by profile: a profile_id that does not exist in rules.apply, rules.format_torrent_file, rules.profiles.update,
rules.rules.list and rules.rules.add is a ValueError with the code `profile_not_found`; an empty profile does not get into the
cache.

Events (out):
  rules.profile.changed {profile_id} — the model RulesProfileChanged. After the write to the database and the reset of the cache,
      on every change of a profile: the update and the deletion of a profile, the addition, the change and the deletion of a rule,
      the reordering (one event for each profile touched). The layout counts again by it on its own (Т-14). The creation of a
      profile gives no event: an empty profile is assigned to no source.

The cache of the compiled profiles is the business of the CRUD alone; it cannot be reset from outside.

The compile of a profile and the answer to the lines of rules.apply are made in the pool of computation (core.compute): a page is
thousands of lines and tenths of a second of work, and the loop of the process serves every module and window. The count itself is
in `calc/rules` (the engine, the numbers, the file names, the answers); here are the profiles and rules in the database, the cache
of the compiled profiles and the events. The file name by the finished numbers is counted here too, without the pool: it is
microseconds of work, a hop to the pool would cost more (the planner asks for the name of every goal)."""
from __future__ import annotations

import sqlite3

from calc.rules.engine import CompiledProfile, compile_profile
from calc.rules.reply import checked, filename_reply, marked, repeated_keys, reply_of, torrent_file_reply
from core import compute
from core.contracts import Created, Done, RulesApplyReply, RulesApplyRequest, RulesProfileChanged
from core.db import Database
from core.envelope import Envelope
from core.module import BaseModule
from core.reasons import reason
from core.topics import (CATALOG_SOURCES_BY_PROFILE, RULES_APPLY, RULES_FORMAT_FILENAME, RULES_FORMAT_TORRENT_FILE,
                         RULES_PROFILE_CHANGED, RULES_PROFILES_CREATE, RULES_PROFILES_DELETE, RULES_PROFILES_EXPORT,
                         RULES_PROFILES_LIST, RULES_PROFILES_UPDATE, RULES_RULES_ADD, RULES_RULES_DELETE,
                         RULES_RULES_LIST, RULES_RULES_REORDER, RULES_RULES_UPDATE)

from .repository import RulesRepository


class RulesModule(BaseModule):
    name = "rules"

    def __init__(self, bus, db: Database) -> None:
        self.repo = RulesRepository(db)
        self._cache: dict[int, CompiledProfile] = {}
        # Bumped on every change of a profile: a compile that read the rules before the change is not cached.
        self._version: dict[int, int] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(RULES_APPLY, self.on_apply)
        self.handle(RULES_PROFILES_EXPORT, self.on_profiles_export)
        self.handle(RULES_PROFILES_LIST, self.on_profiles_list)
        self.handle(RULES_FORMAT_FILENAME, self.on_format_filename)
        self.handle(RULES_FORMAT_TORRENT_FILE, self.on_format_torrent_file)
        self.handle(RULES_PROFILES_CREATE, self.on_profile_create)
        self.handle(RULES_PROFILES_UPDATE, self.on_profile_update)
        self.handle(RULES_PROFILES_DELETE, self.on_profile_delete)
        self.handle(RULES_RULES_LIST, self.on_rules_list)
        self.handle(RULES_RULES_ADD, self.on_rule_add)
        self.handle(RULES_RULES_UPDATE, self.on_rule_update)
        self.handle(RULES_RULES_DELETE, self.on_rule_delete)
        self.handle(RULES_RULES_REORDER, self.on_rules_reorder)

    async def _require_profile(self, profile_id: int) -> None:
        """The single refusal for an operation on a profile that is not there."""
        if not await self.repo.profile_exists(profile_id):
            raise ValueError(reason("profile_not_found", profile_id=profile_id))

    async def _profile(self, profile_id: int) -> CompiledProfile:
        if profile_id in self._cache:
            return self._cache[profile_id]
        await self._require_profile(profile_id)
        version = self._version.get(profile_id, 0)
        raw_rules = await self.repo.get_rules(profile_id)
        profile = await compute.run(compile_profile, profile_id, raw_rules)
        for bad in profile.invalid_rules:
            self.log.error(
                "профиль %d: правило «%s» (id=%s) неисправно и "
                "отключено: %s", profile_id, bad["name"],
                bad["rule_id"], bad["error"])
        if self._version.get(profile_id, 0) == version:
            self._cache[profile_id] = profile
        return profile

    def _profile_changed(self, profile_id: int) -> None:
        """Forgets the compiled profile, then announces the change; called after the write."""
        self._version[profile_id] = self._version.get(profile_id, 0) + 1
        self._cache.pop(profile_id, None)
        self.publish_event(RULES_PROFILE_CHANGED, RulesProfileChanged(profile_id=profile_id).model_dump())

    # --- queries -------------------------------------------------------------

    async def on_apply(self, env: Envelope) -> RulesApplyReply:
        request = await compute.run(RulesApplyRequest.model_validate, env.payload)
        if repeated := repeated_keys(request):
            raise ValueError(reason("apply_keys_repeated", keys=repeated))
        if request.rules is None:
            profile = await self._profile(request.profile_id)
        else:  # the profile as a run took it: what was saved since does not count for that run
            profile = await compute.run(compile_profile, request.profile_id, request.rules)
        # A page holds thousands of lines (3649 at most on 2026-11-10, 0.1–0.2 s of work): the loop of the service, which
        # serves every module and window, does not wait for them.
        return await compute.run(reply_of, profile, request)

    async def on_profiles_export(self, env: Envelope) -> dict:
        """The stored rules of the profiles named, as `rules.apply` takes them back (RunSnapshot.profiles)."""
        profiles = {}
        for profile_id in sorted({int(each) for each in env.payload.get("profile_ids", [])}):
            await self._require_profile(profile_id)
            profiles[profile_id] = await self.repo.get_rules(profile_id)
        return {"profiles": profiles}

    async def on_profiles_list(self, env: Envelope) -> list[dict]:
        """{kind?} → the profiles of that kind (`series` when not said: the profiles of the torrent editor are not
        profiles of a series)."""
        return await self.repo.list_profiles((env.payload or {}).get("kind") or "series")

    # --- constructor CRUD: each write resets the cache and announces the change ---

    async def on_profile_create(self, env: Envelope) -> Created:
        profile_id = await self.repo.create_profile(env.payload["name"], env.payload.get("kind") or "series")
        return Created(id=profile_id)

    async def on_profile_update(self, env: Envelope) -> Done:
        profile_id = env.payload["profile_id"]
        await self._require_profile(profile_id)
        await self.repo.update_profile(profile_id, env.payload["data"])
        self._profile_changed(profile_id)
        return Done()

    async def on_profile_delete(self, env: Envelope) -> Done:
        profile_id = int(env.payload["profile_id"])
        used = (await self.reply_of(CATALOG_SOURCES_BY_PROFILE, {"profile_id": profile_id})).sources
        if used:
            raise ValueError(reason("profile_in_use", profile_id=profile_id,
                                    sources=len(used)))
        try:
            deleted = await self.repo.delete_profile(profile_id)
        except sqlite3.IntegrityError:
            # A source took the profile after the question: the transaction is rolled back, so ask again.
            used = (await self.reply_of(CATALOG_SOURCES_BY_PROFILE, {"profile_id": profile_id})).sources
            if not used:
                raise
            raise ValueError(reason("profile_in_use", profile_id=profile_id,
                                    sources=len(used))) from None
        if deleted:
            self._profile_changed(profile_id)
        return Done()

    async def on_rules_list(self, env: Envelope) -> list[dict]:
        profile_id = int(env.payload["profile_id"])
        profile = await self._profile(profile_id)
        return marked(await self.repo.get_rules(profile_id), profile.invalid_rules)

    async def on_rule_add(self, env: Envelope) -> Created:
        profile_id = env.payload["profile_id"]
        await self._require_profile(profile_id)
        checked(env.payload.get("data") or {})
        rule_id = await self.repo.add_rule(profile_id, env.payload["data"])
        self._profile_changed(profile_id)
        return Created(id=rule_id)

    async def on_rule_update(self, env: Envelope) -> Done:
        rule_id = env.payload["rule_id"]
        checked(env.payload.get("data") or {})
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.update_rule(rule_id, env.payload["data"])
        if profile_id is not None:
            self._profile_changed(profile_id)
        return Done()

    async def on_rule_delete(self, env: Envelope) -> Done:
        rule_id = env.payload["rule_id"]
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.delete_rule(rule_id)
        if profile_id is not None:
            self._profile_changed(profile_id)
        return Done()

    async def on_rules_reorder(self, env: Envelope) -> Done:
        ordered_ids = env.payload["ordered_ids"]
        await self.repo.reorder_rules(ordered_ids)
        for profile_id in await self.repo.profile_ids_of_rules(ordered_ids):
            self._profile_changed(profile_id)
        return Done()

    # --- file names ----------------------------------------------------------------

    async def on_format_filename(self, env: Envelope) -> dict:
        """The name comes from the numbers of media_item and the series card; no rule is applied, no profile read."""
        p = env.payload
        return filename_reply(p["series"], p["media_item"], p.get("episode_override"), p.get("original_filename"))

    async def on_format_torrent_file(self, env: Envelope) -> dict:
        """Torrent file name in its 'Season NN' folder; the profile comes from the request, not from the series."""
        p = env.payload
        if p.get("profile_id") is None:
            raise ValueError(reason("torrent_profile_missing"))
        profile = await self._profile(int(p["profile_id"]))
        return torrent_file_reply(profile, p["series"], p["file_basename"], p["original_path"])
