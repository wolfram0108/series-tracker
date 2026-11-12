"""Repository of rules: parser_profiles, parser_rules, parser_rule_conditions.

A write of several statements is one transaction: a profile never stays without its rules,
a rule without its conditions, and a new order of rules is applied whole.
"""
from __future__ import annotations

import json
import sqlite3

from core.db import Database
from core.reasons import reason


def _set_conditions(conn: sqlite3.Connection, rule_id: int,
                    conditions: list[dict] | None) -> None:
    for cond in conditions or []:
        conn.execute(
            "INSERT INTO parser_rule_conditions (rule_id, condition_type, "
            "pattern, logical_operator) VALUES (?, ?, ?, ?)",
            (rule_id, cond.get("condition_type"), cond.get("pattern"),
             cond.get("logical_operator", "AND")))


def _table_text(table: dict | None) -> str | None:
    """The rows of a rule «Таблица» as stored; None for an ordinary rule."""
    return None if table is None else json.dumps(table, ensure_ascii=False)


class RulesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_profiles(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, name FROM parser_profiles ORDER BY id")

    async def get_rules(self, profile_id: int) -> list[dict]:
        rules = await self._db.fetch_all(
            "SELECT id, profile_id, name, priority, action_pattern, "
            "continue_after_match, rule_table FROM parser_rules "
            "WHERE profile_id=? ORDER BY priority", (profile_id,))
        self._db.coerce_bools(rules, ("continue_after_match",))
        for rule in rules:
            rule["rule_table"] = json.loads(rule["rule_table"]) if rule["rule_table"] else None
            rule["conditions"] = await self._db.fetch_all(
                "SELECT id, condition_type, pattern, logical_operator "
                "FROM parser_rule_conditions WHERE rule_id=? ORDER BY id",
                (rule["id"],))
        return rules

    # --- CRUD конструктора (Р-22) ----------------------------------------------

    async def create_profile(self, name: str) -> int:
        if await self._db.fetch_one(
                "SELECT id FROM parser_profiles WHERE name=?", (name,)):
            raise ValueError(reason("profile_name_taken", name=name))
        return await self._db.insert(
            "INSERT INTO parser_profiles (name) VALUES (?)", (name,))

    async def profile_exists(self, profile_id: int) -> bool:
        return await self._db.fetch_one(
            "SELECT 1 FROM parser_profiles WHERE id=?", (profile_id,)) is not None

    async def update_profile(self, profile_id: int, data: dict) -> None:
        if "name" in data:
            dup = await self._db.fetch_one(
                "SELECT id FROM parser_profiles WHERE name=? AND id<>?",
                (data["name"], profile_id))
            if dup:
                raise ValueError(reason("profile_name_taken", name=data["name"]))
            await self._db.execute(
                "UPDATE parser_profiles SET name=? WHERE id=?",
                (data["name"], profile_id))

    async def delete_profile(self, profile_id: int) -> bool:
        """Removes the profile with its rules and conditions in one transaction.

        A refusal of the last delete (a source still points at the profile)
        takes the deleted rules back. True when the profile existed.
        """
        def work(conn: sqlite3.Connection) -> bool:
            conn.execute(
                "DELETE FROM parser_rule_conditions WHERE rule_id IN "
                "(SELECT id FROM parser_rules WHERE profile_id=?)",
                (profile_id,))
            conn.execute("DELETE FROM parser_rules WHERE profile_id=?",
                         (profile_id,))
            return conn.execute("DELETE FROM parser_profiles WHERE id=?",
                                (profile_id,)).rowcount > 0
        return await self._db.transaction(work)

    async def add_rule(self, profile_id: int, data: dict) -> int:
        def work(conn: sqlite3.Connection) -> int:
            top = conn.execute(
                "SELECT COALESCE(MAX(priority), 0) FROM parser_rules "
                "WHERE profile_id=?", (profile_id,)).fetchone()[0]
            rule_id = conn.execute(
                "INSERT INTO parser_rules (profile_id, name, priority, "
                "action_pattern, continue_after_match, rule_table) VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, data.get("name", "Новое правило"), top + 1,
                 data.get("action_pattern", "[]"),
                 1 if data.get("continue_after_match") else 0,
                 _table_text(data.get("rule_table")))).lastrowid
            _set_conditions(conn, rule_id, data.get("conditions"))
            return rule_id
        return await self._db.transaction(work)

    async def update_rule(self, rule_id: int, data: dict) -> None:
        def work(conn: sqlite3.Connection) -> None:
            rule = conn.execute("SELECT * FROM parser_rules WHERE id=?",
                                (rule_id,)).fetchone()
            if not rule:
                return
            conn.execute(
                "UPDATE parser_rules SET name=?, action_pattern=?, "
                "continue_after_match=?, rule_table=? WHERE id=?",
                (data.get("name", rule["name"]),
                 data.get("action_pattern", rule["action_pattern"]),
                 1 if data.get("continue_after_match",
                               rule["continue_after_match"]) else 0,
                 _table_text(data["rule_table"]) if "rule_table" in data else rule["rule_table"],
                 rule_id))
            # Absent conditions leave the stored ones alone; an empty list clears them.
            if data.get("conditions") is not None:
                conn.execute("DELETE FROM parser_rule_conditions "
                             "WHERE rule_id=?", (rule_id,))
                _set_conditions(conn, rule_id, data["conditions"])
        await self._db.transaction(work)

    async def delete_rule(self, rule_id: int) -> None:
        await self._db.execute_many([
            ("DELETE FROM parser_rule_conditions WHERE rule_id=?", (rule_id,)),
            ("DELETE FROM parser_rules WHERE id=?", (rule_id,))])

    async def reorder_rules(self, ordered_ids: list[int]) -> None:
        await self._db.execute_many([
            ("UPDATE parser_rules SET priority=? WHERE id=?", (index, rule_id))
            for index, rule_id in enumerate(ordered_ids)])

    async def profile_id_of_rule(self, rule_id: int) -> int | None:
        row = await self._db.fetch_one(
            "SELECT profile_id FROM parser_rules WHERE id=?", (rule_id,))
        return row["profile_id"] if row else None

    async def profile_ids_of_rules(self, rule_ids: list[int]) -> list[int]:
        """Distinct profiles that own the given rules, ascending."""
        if not rule_ids:
            return []
        marks = ",".join("?" * len(rule_ids))
        rows = await self._db.fetch_all(
            f"SELECT DISTINCT profile_id FROM parser_rules "
            f"WHERE id IN ({marks}) ORDER BY profile_id", rule_ids)
        return [row["profile_id"] for row in rows]
