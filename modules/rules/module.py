"""Модуль rules — владелец профилей правил (Р-8).

Этап 3: применение правил (query rules.apply) с кэшем скомпилированных
профилей. CRUD профилей/правил и тестирование из конструктора UI —
этап 5 (gateway).

Неисправные правила (решение Б): при компиляции профиля попадают в
invalid_rules, не применяются, пишутся в лог ошибкой и возвращаются
в каждом ответе rules.apply — проблема видна, а не замолчана.
"""
from __future__ import annotations

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from . import formatter
from .engine import CompiledProfile, compile_profile, process_title
from .repository import RulesRepository


class RulesModule(BaseModule):
    name = "rules"

    def __init__(self, bus, db: Database) -> None:
        self.repo = RulesRepository(db)
        self._cache: dict[int, CompiledProfile] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("rules.apply", self.on_apply)
        self.handle("rules.profiles.list", self.on_profiles_list)
        self.handle("rules.cache.invalidate", self.on_cache_invalidate)
        self.handle("rules.format_filename", self.on_format_filename)
        self.handle("rules.format_torrent_file", self.on_format_torrent_file)
        self.handle("rules.profiles.create", self.on_profile_create)
        self.handle("rules.profiles.update", self.on_profile_update)
        self.handle("rules.profiles.delete", self.on_profile_delete)
        self.handle("rules.rules.list", self.on_rules_list)
        self.handle("rules.rules.add", self.on_rule_add)
        self.handle("rules.rules.update", self.on_rule_update)
        self.handle("rules.rules.delete", self.on_rule_delete)
        self.handle("rules.rules.reorder", self.on_rules_reorder)
        self.handle("rules.test", self.on_test)

    async def _profile(self, profile_id: int) -> CompiledProfile:
        if profile_id not in self._cache:
            raw_rules = await self.repo.get_rules(profile_id)
            profile = compile_profile(profile_id, raw_rules)
            for bad in profile.invalid_rules:
                self.log.error(
                    "профиль %d: правило «%s» (id=%s) неисправно и "
                    "отключено: %s", profile_id, bad["name"],
                    bad["rule_id"], bad["error"])
            self._cache[profile_id] = profile
        return self._cache[profile_id]

    # --- queries -------------------------------------------------------------

    async def on_apply(self, env: Envelope) -> dict:
        """payload: {profile_id, titles: [str, ...]}
        reply:   {results: [{title, excluded, extracted, events, errors}],
                  invalid_rules: [...]}"""
        profile = await self._profile(int(env.payload["profile_id"]))
        results = []
        for title in env.payload["titles"]:
            outcome = process_title(profile, title)
            outcome["title"] = title
            results.append(outcome)
        return {"results": results, "invalid_rules": profile.invalid_rules}

    async def on_profiles_list(self, env: Envelope) -> list[dict]:
        return await self.repo.list_profiles()

    # --- CRUD конструктора (Р-22); кэш инвалидируется сам ----------------------

    async def on_profile_create(self, env: Envelope) -> dict:
        profile_id = await self.repo.create_profile(env.payload["name"])
        return {"id": profile_id}

    async def on_profile_update(self, env: Envelope) -> dict:
        profile_id = env.payload["profile_id"]
        await self.repo.update_profile(profile_id, env.payload["data"])
        self._cache.pop(profile_id, None)
        return {"ok": True}

    async def on_profile_delete(self, env: Envelope) -> dict:
        profile_id = env.payload["profile_id"]
        await self.repo.delete_profile(profile_id)
        self._cache.pop(profile_id, None)
        return {"ok": True}

    async def on_rules_list(self, env: Envelope) -> list[dict]:
        return await self.repo.get_rules(env.payload["profile_id"])

    async def on_rule_add(self, env: Envelope) -> dict:
        profile_id = env.payload["profile_id"]
        rule_id = await self.repo.add_rule(profile_id, env.payload["data"])
        self._cache.pop(profile_id, None)
        return {"id": rule_id}

    async def on_rule_update(self, env: Envelope) -> dict:
        rule_id = env.payload["rule_id"]
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.update_rule(rule_id, env.payload["data"])
        self._cache.pop(profile_id, None)
        return {"ok": True}

    async def on_rule_delete(self, env: Envelope) -> dict:
        rule_id = env.payload["rule_id"]
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.delete_rule(rule_id)
        self._cache.pop(profile_id, None)
        return {"ok": True}

    async def on_rules_reorder(self, env: Envelope) -> dict:
        await self.repo.reorder_rules(env.payload["ordered_ids"])
        self._cache.clear()  # порядок мог затронуть несколько профилей
        return {"ok": True}

    async def on_test(self, env: Envelope) -> list[dict]:
        """Тест конструктора (контракт старого POST /test): полные
        video-объекты → [{source_data, match_events, result}]."""
        profile = await self._profile(int(env.payload["profile_id"]))
        results = []
        for video in env.payload["videos"]:
            outcome = process_title(profile, video.get("title") or "")
            results.append({
                "source_data": video,
                "match_events": outcome.get("events") or [],
                "result": {"extracted": outcome.get("extracted") or {},
                           "excluded": outcome.get("excluded", False)},
            })
        return results

    async def on_format_filename(self, env: Envelope) -> dict:
        """payload: {series, media_item, episode_override?, original_filename?}
        Полная цепочка имени VK-элемента: правила по source_title →
        метаданные (иерархия приоритетов) → формат (Р-15)."""
        p = env.payload
        series, item = p["series"], p["media_item"]
        extracted: dict = {}
        title = item.get("source_title")
        if title and series.get("parser_profile_id"):
            profile = await self._profile(int(series["parser_profile_id"]))
            extracted = process_title(profile, title).get("extracted") or {}
        metadata = formatter.build_metadata(series, item, extracted)
        if (override := p.get("episode_override")) is not None:
            metadata["episode"] = override
            metadata.pop("start", None)
            metadata.pop("end", None)
        return {"filename": formatter.format_name(
            series, metadata, original_filename=p.get("original_filename"))}

    async def on_format_torrent_file(self, env: Envelope) -> dict:
        """payload: {series, file_basename, original_path}
        Имя файла торрента с сезонной папкой (логика renaming_processor):
        правила по имени файла → 'Season NN/строгое имя.ext'.
        filename=None — сезон не определить, файл пропускается."""
        p = env.payload
        series = p["series"]
        profile = await self._profile(int(series["parser_profile_id"]))
        extracted = process_title(profile,
                                  p["file_basename"]).get("extracted") or {}
        season = formatter.torrent_season_folder(series, extracted,
                                                 p["original_path"])
        if season is None:
            return {"filename": None, "extracted": extracted}
        name = formatter.format_name(
            series, extracted, original_filename=p["file_basename"],
            target_directory=f"Season {season:02d}")
        return {"filename": name, "extracted": extracted}

    async def on_cache_invalidate(self, env: Envelope) -> dict:
        """Сброс кэша (после CRUD правил — этап 5 будет звать сам)."""
        profile_id = (env.payload or {}).get("profile_id")
        if profile_id is None:
            self._cache.clear()
        else:
            self._cache.pop(int(profile_id), None)
        return {"ok": True}
