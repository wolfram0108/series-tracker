"""Модуль rules — владелец профилей правил и конструктора номеров (Р-8, этап 10).

Конструктор — единственный источник номера серии, сезона, диапазона и части (З-3):
раскладка отправляет ему строки с ключами и получает ответ по ключу. Имя файла
номеров не вычисляет и правил второй раз не применяет. Профиль назначен
источнику сериала в catalog; модуль таблиц других модулей не читает — профиль
приходит параметром запроса, использование профиля спрашивается у catalog.

Queries:
  rules.apply {profile_id, lines, series_season?, rules?}
      → {results, invalid_rules: [...]}
      — модели RulesApplyRequest / RulesApplyReply. Строки и ответы — записи
      по столбцам (core.native): строки — key, text, player, voice (список
      RuleLine приводится к ним моделью запроса); ответ строки — excluded,
      season, episode, start, end, part, voiceover, events, errors и прочие
      извлечённые, по ключу — RulesApplyReply.by_key(). series_season — сезон
      карточки сериала, запасной, когда правило сезон не извлекло (первое
      число его текста; без обоих — сезон 1); у исключённой строки номеров нет.
      Одиночная серия без части — часть 1, где у её места (плеер, озвучка,
      сезон, серия) есть одиночные серии с частями. voiceover — результат
      действия «назначить озвучку». Повторяющиеся ключи в запросе — отказ
      ValueError (`apply_keys_repeated`); нет строк — пустой ответ.
      Неисправные правила (решение Б) не применяются и возвращаются в
      invalid_rules каждого ответа и пишутся в лог ошибкой.
  rules.format_filename {series, media_item, episode_override?, original_filename?}
      → {filename}
      — имя по готовым номерам media_item (season, episode_start/end, part,
      resolution, voiceover_tag) и карточке сериала; профиль не читается.
      series.kind == 'video' даёт расширение .mp4.
  rules.format_torrent_file {series, profile_id, file_basename, original_path}
      → {filename, extracted}
      — имя файла торрента с сезонной папкой; правила профиля profile_id
      (обязателен, иначе ValueError) применяются к имени файла.
      filename=None — сезон не определить, файл пропускается.
  rules.profiles.list {kind?} → [profile] (kind: series — по умолчанию, или torrent — профили редактора торрента)
  rules.profiles.create {name, kind?} → {id}
  rules.profiles.update {profile_id, data} → {ok}
  rules.profiles.delete {profile_id} → {ok}
      — профиль и его правила удаляются одной транзакцией; профиль, назначенный
      источникам (catalog.sources.by_profile), удалить нельзя: ValueError с
      кодом `profile_in_use` (core/reasons). Источник, занявший профиль между
      вопросом и удалением, ловится внешним ключом: транзакция откатывается,
      catalog спрашивается снова и отказ тот же; нет источников — ошибка БД как есть.
      Удаление уже отсутствующего профиля не ошибка: ничего не изменилось, события нет.
  rules.rules.list {profile_id} → [rule]  (с признаками valid / error)
  rules.rules.add {profile_id, data} → {id}
  rules.rules.update {rule_id, data} → {ok}
  rules.rules.delete {rule_id} → {ok}
  rules.rules.reorder {ordered_ids} → {ok}
      — правило вида «Таблица» несёт `rule_table` {action_type, rows: [{if: блоки, then: значение | блоки, action_type?}], language?}
        (`action_type` строки — её своё действие вместо действия колонки)
        (`language` — язык по умолчанию строк таблицы каталогов без своего языка)
      (у обычного правила null): строки — те же правила «если содержит → действие столбца», движок
      раскладывает их по порядку на месте таблицы (engine.table_rules); строка без условия или значения —
      отказ с её номером.

Отказы по профилю: несуществующий profile_id в rules.apply, rules.format_torrent_file,
rules.profiles.update, rules.rules.list и rules.rules.add — ValueError с кодом
`profile_not_found`; пустой профиль в кэш не попадает.

Events (исходящие):
  rules.profile.changed {profile_id} — модель RulesProfileChanged. После
      записи в БД и сброса кэша на каждом изменении профиля: обновление и
      удаление профиля, добавление, изменение и удаление правила,
      переупорядочивание (по событию на каждый затронутый профиль). Раскладка
      пересчитывается по нему сама (Т-14). Создание профиля события не даёт:
      пустой профиль ни у какого источника не назначен.

Кэш скомпилированных профилей — внутреннее дело CRUD; снаружи его не сбросить.

Движок правил — скомпилированный (core.native): профиль собирается один раз, строки читаются им без интерпретатора.
Сборка профиля и ответ на строки rules.apply идут в пуле вычислений (core.compute): цикл процесса обслуживает все модули и
окна и их не ждёт.
"""
from __future__ import annotations

import sqlite3

from core import BaseModule, compute
from core.contracts import RulesApplyRequest, RulesProfileChanged
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason

from . import formatter
from .engine import CompiledProfile, compile_profile, process_title, validate_rule
from .repository import RulesRepository

def answer_lines(profile: CompiledProfile, lines: bytes, card: str | None) -> bytes:
    """The answer of the profile to the lines (columns in, columns out): every line read by the compiled engine at once."""
    return profile.compiled.apply(lines, card)


class RulesModule(BaseModule):
    name = "rules"

    def __init__(self, bus, db: Database) -> None:
        self.repo = RulesRepository(db)
        self._cache: dict[int, CompiledProfile] = {}
        # Bumped on every change of a profile: a compile that read the rules before the change is not cached.
        self._version: dict[int, int] = {}
        super().__init__(bus)

    def register(self) -> None:
        # The answers to the sources of a series are independent: they are made side by side (the engine lets the interpreter go).
        self.handle("rules.apply", self.on_apply, concurrent=True)
        self.handle("rules.profiles.export", self.on_profiles_export)
        self.handle("rules.profiles.list", self.on_profiles_list)
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
        self.publish_event("rules.profile.changed",
                           RulesProfileChanged(profile_id=profile_id).model_dump())

    # --- queries -------------------------------------------------------------

    async def on_apply(self, env: Envelope) -> dict:
        request = RulesApplyRequest.model_validate(env.payload)
        if request.rules is None:
            profile = await self._profile(request.profile_id)
        else:  # the profile as a run took it: what was saved since does not count for that run
            profile = await compute.run(compile_profile, request.profile_id, request.rules)
        # A page holds thousands of lines: the loop of the service, which serves every module and window, does not wait for
        # them. Keys that repeat are refused (`apply_keys_repeated`).
        results = await compute.run(answer_lines, profile, request.lines, request.card)
        return {"results": results, "invalid_rules": profile.invalid_rules}

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

    async def on_profile_create(self, env: Envelope) -> dict:
        profile_id = await self.repo.create_profile(env.payload["name"], env.payload.get("kind") or "series")
        return {"id": profile_id}

    async def on_profile_update(self, env: Envelope) -> dict:
        profile_id = env.payload["profile_id"]
        await self._require_profile(profile_id)
        await self.repo.update_profile(profile_id, env.payload["data"])
        self._profile_changed(profile_id)
        return {"ok": True}

    async def on_profile_delete(self, env: Envelope) -> dict:
        profile_id = int(env.payload["profile_id"])
        used = await self.request("catalog.sources.by_profile",
                                  {"profile_id": profile_id})
        if used:
            raise ValueError(reason("profile_in_use", profile_id=profile_id,
                                    sources=len(used)))
        try:
            deleted = await self.repo.delete_profile(profile_id)
        except sqlite3.IntegrityError:
            # A source took the profile after the question: the transaction is rolled back, so ask again.
            used = await self.request("catalog.sources.by_profile",
                                      {"profile_id": profile_id})
            if not used:
                raise
            raise ValueError(reason("profile_in_use", profile_id=profile_id,
                                    sources=len(used))) from None
        if deleted:
            self._profile_changed(profile_id)
        return {"ok": True}

    async def on_rules_list(self, env: Envelope) -> list[dict]:
        profile_id = int(env.payload["profile_id"])
        profile = await self._profile(profile_id)
        rules = await self.repo.get_rules(profile_id)
        errors = {bad["rule_id"]: bad["error"] for bad in profile.invalid_rules}
        for rule in rules:
            error = errors.get(rule.get("id"))
            rule["valid"] = error is None
            rule["error"] = error
        return rules

    @staticmethod
    def _checked(data: dict) -> None:
        """A rule the engine cannot compile is refused to the user in the engine's words: a refusal, not a failure of
        the module (no trace in the journal)."""
        try:
            validate_rule(data)
        except ValueError as exc:
            raise ValueError(reason("rule_invalid", detail=str(exc))) from None

    async def on_rule_add(self, env: Envelope) -> dict:
        profile_id = env.payload["profile_id"]
        await self._require_profile(profile_id)
        self._checked(env.payload.get("data") or {})
        rule_id = await self.repo.add_rule(profile_id, env.payload["data"])
        self._profile_changed(profile_id)
        return {"id": rule_id}

    async def on_rule_update(self, env: Envelope) -> dict:
        rule_id = env.payload["rule_id"]
        self._checked(env.payload.get("data") or {})
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.update_rule(rule_id, env.payload["data"])
        if profile_id is not None:
            self._profile_changed(profile_id)
        return {"ok": True}

    async def on_rule_delete(self, env: Envelope) -> dict:
        rule_id = env.payload["rule_id"]
        profile_id = await self.repo.profile_id_of_rule(rule_id)
        await self.repo.delete_rule(rule_id)
        if profile_id is not None:
            self._profile_changed(profile_id)
        return {"ok": True}

    async def on_rules_reorder(self, env: Envelope) -> dict:
        ordered_ids = env.payload["ordered_ids"]
        await self.repo.reorder_rules(ordered_ids)
        for profile_id in await self.repo.profile_ids_of_rules(ordered_ids):
            self._profile_changed(profile_id)
        return {"ok": True}

    # --- file names ----------------------------------------------------------------

    async def on_format_filename(self, env: Envelope) -> dict:
        """The name comes from the numbers of media_item and the series card; no rule is applied, no profile read."""
        p = env.payload
        series, item = p["series"], p["media_item"]
        metadata = formatter.build_metadata(series, item)
        if (override := p.get("episode_override")) is not None:
            metadata["episode"] = override
            metadata.pop("start", None)
            metadata.pop("end", None)
        return {"filename": formatter.format_name(
            series, metadata, original_filename=p.get("original_filename"))}

    async def on_format_torrent_file(self, env: Envelope) -> dict:
        """Torrent file name in its 'Season NN' folder; the profile comes from the request, not from the series."""
        p = env.payload
        series = p["series"]
        if p.get("profile_id") is None:
            raise ValueError(reason("torrent_profile_missing"))
        profile = await self._profile(int(p["profile_id"]))
        extracted = process_title(profile,
                                  p["file_basename"]).get("extracted") or {}
        season = formatter.torrent_season_folder(series, extracted,
                                                 p["original_path"])
        if season is None:
            return {"filename": None, "extracted": extracted}
        # резолвленный сезон (карточка сериала могла его переопределить)
        # обязан попасть в extracted_metadata — иначе композиция/агрегаты
        # season, читающие persisted-запись, увидят сырое значение.
        extracted["season"] = season
        formatter.merge_series_defaults(series, extracted, extracted)
        name = formatter.format_name(
            series, extracted, original_filename=p["file_basename"],
            target_directory=f"Season {season:02d}")
        return {"filename": name, "extracted": extracted}
