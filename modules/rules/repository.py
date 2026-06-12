"""Репозиторий rules: parser_profiles, parser_rules, parser_rule_conditions."""
from __future__ import annotations

from core.db import Database


class RulesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_profiles(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, name, preferred_voiceovers FROM parser_profiles "
            "ORDER BY id")

    async def get_rules(self, profile_id: int) -> list[dict]:
        rules = await self._db.fetch_all(
            "SELECT id, profile_id, name, priority, action_pattern, "
            "continue_after_match FROM parser_rules "
            "WHERE profile_id=? ORDER BY priority", (profile_id,))
        for rule in rules:
            rule["conditions"] = await self._db.fetch_all(
                "SELECT id, condition_type, pattern, logical_operator "
                "FROM parser_rule_conditions WHERE rule_id=? ORDER BY id",
                (rule["id"],))
        return rules

    # --- CRUD конструктора (Р-22) ----------------------------------------------

    async def create_profile(self, name: str) -> int:
        if await self._db.fetch_one(
                "SELECT id FROM parser_profiles WHERE name=?", (name,)):
            raise ValueError(f"Профиль парсера с именем '{name}' уже "
                             "существует.")
        await self._db.execute(
            "INSERT INTO parser_profiles (name, preferred_voiceovers) "
            "VALUES (?, '')", (name,))
        row = await self._db.fetch_one(
            "SELECT id FROM parser_profiles ORDER BY id DESC LIMIT 1")
        return row["id"]

    async def update_profile(self, profile_id: int, data: dict) -> None:
        if not await self._db.fetch_one(
                "SELECT id FROM parser_profiles WHERE id=?", (profile_id,)):
            raise ValueError(f"Профиль с ID {profile_id} не найден.")
        if "name" in data:
            dup = await self._db.fetch_one(
                "SELECT id FROM parser_profiles WHERE name=? AND id<>?",
                (data["name"], profile_id))
            if dup:
                raise ValueError(f"Профиль парсера с именем "
                                 f"'{data['name']}' уже существует.")
            await self._db.execute(
                "UPDATE parser_profiles SET name=? WHERE id=?",
                (data["name"], profile_id))
        if "preferred_voiceovers" in data:
            await self._db.execute(
                "UPDATE parser_profiles SET preferred_voiceovers=? "
                "WHERE id=?", (data["preferred_voiceovers"], profile_id))

    async def delete_profile(self, profile_id: int) -> None:
        used = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM series WHERE parser_profile_id=?",
            (profile_id,))
        if used and used["n"]:
            raise ValueError("Невозможно удалить профиль, так как он "
                             f"используется в {used['n']} сериалах.")
        await self._db.execute(
            "DELETE FROM parser_rule_conditions WHERE rule_id IN "
            "(SELECT id FROM parser_rules WHERE profile_id=?)",
            (profile_id,))
        await self._db.execute(
            "DELETE FROM parser_rules WHERE profile_id=?", (profile_id,))
        await self._db.execute(
            "DELETE FROM parser_profiles WHERE id=?", (profile_id,))

    async def add_rule(self, profile_id: int, data: dict) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(priority), 0) AS p FROM parser_rules "
            "WHERE profile_id=?", (profile_id,))
        await self._db.execute(
            "INSERT INTO parser_rules (profile_id, name, priority, "
            "action_pattern, continue_after_match) VALUES (?, ?, ?, ?, ?)",
            (profile_id, data.get("name", "Новое правило"), row["p"] + 1,
             data.get("action_pattern", "[]"),
             1 if data.get("continue_after_match") else 0))
        rule = await self._db.fetch_one(
            "SELECT id FROM parser_rules ORDER BY id DESC LIMIT 1")
        await self._set_conditions(rule["id"], data.get("conditions"))
        return rule["id"]

    async def update_rule(self, rule_id: int, data: dict) -> None:
        rule = await self._db.fetch_one(
            "SELECT * FROM parser_rules WHERE id=?", (rule_id,))
        if not rule:
            return
        await self._db.execute(
            "UPDATE parser_rules SET name=?, action_pattern=?, "
            "continue_after_match=? WHERE id=?",
            (data.get("name", rule["name"]),
             data.get("action_pattern", rule["action_pattern"]),
             1 if data.get("continue_after_match",
                           rule["continue_after_match"]) else 0,
             rule_id))
        await self._db.execute(
            "DELETE FROM parser_rule_conditions WHERE rule_id=?",
            (rule_id,))
        await self._set_conditions(rule_id, data.get("conditions"))

    async def _set_conditions(self, rule_id: int,
                              conditions: list[dict] | None) -> None:
        for cond in conditions or []:
            await self._db.execute(
                "INSERT INTO parser_rule_conditions (rule_id, "
                "condition_type, pattern, logical_operator) "
                "VALUES (?, ?, ?, ?)",
                (rule_id, cond.get("condition_type"), cond.get("pattern"),
                 cond.get("logical_operator", "AND")))

    async def delete_rule(self, rule_id: int) -> None:
        await self._db.execute(
            "DELETE FROM parser_rule_conditions WHERE rule_id=?",
            (rule_id,))
        await self._db.execute(
            "DELETE FROM parser_rules WHERE id=?", (rule_id,))

    async def reorder_rules(self, ordered_ids: list[int]) -> None:
        for index, rule_id in enumerate(ordered_ids):
            await self._db.execute(
                "UPDATE parser_rules SET priority=? WHERE id=?",
                (index, rule_id))

    async def profile_id_of_rule(self, rule_id: int) -> int | None:
        row = await self._db.fetch_one(
            "SELECT profile_id FROM parser_rules WHERE id=?", (rule_id,))
        return row["profile_id"] if row else None
