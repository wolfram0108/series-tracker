"""Репозиторий rules: parser_profiles, parser_rules, parser_rule_conditions."""
from __future__ import annotations

from core.db import Database


class RulesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_profiles(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, name FROM parser_profiles ORDER BY id")

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
