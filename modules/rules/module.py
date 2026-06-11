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

    async def on_cache_invalidate(self, env: Envelope) -> dict:
        """Сброс кэша (после CRUD правил — этап 5 будет звать сам)."""
        profile_id = (env.payload or {}).get("profile_id")
        if profile_id is None:
            self._cache.clear()
        else:
            self._cache.pop(int(profile_id), None)
        return {"ok": True}
