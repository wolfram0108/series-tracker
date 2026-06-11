"""Дифф старого и нового движков правил на реальных данных фикстуры.

Запуск:  .venv/bin/python tests/verify_rules_diff.py

Старый rule_engine.py импортируется из main-чекаута (прод не участвует,
это чтение кода с диска). Входы: все source_title из media_items и все
имена файлов из torrent_files, каждый — через профиль своего сериала.

Ожидаемые расхождения (согласованные исправления Р-8):
- фикс А: новый извлекает то, что старый молча терял (IndexError);
- фикс В: новый продолжает каскад там, где старый глох без результата.
Любое расхождение вне этих категорий — повод для разбора.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLD_ROOT = "/home/user/series-tracker"

from modules.rules.engine import compile_profile, process_title  # noqa: E402


# --- старый движок без Flask/БД: шиммируем его зависимости --------------------

class _ShimLogger:
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass


class _ShimDb:
    def __init__(self, rules_by_profile):
        self._rules = rules_by_profile

    def get_rules_for_profile(self, profile_id):
        return self._rules.get(profile_id, [])


def load_old_engine(rules_by_profile):
    old_path = os.path.join(OLD_ROOT, "rule_engine.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("old_rule_engine", old_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RuleEngine(_ShimDb(rules_by_profile), _ShimLogger())


# --- данные ---------------------------------------------------------------------

def load_fixture():
    db = sqlite3.connect("tests/fixtures/app.fixture.db")
    db.row_factory = sqlite3.Row

    rules_by_profile: dict[int, list[dict]] = {}
    for p in db.execute("SELECT id FROM parser_profiles"):
        rules = []
        for r in db.execute(
                "SELECT * FROM parser_rules WHERE profile_id=? "
                "ORDER BY priority", (p["id"],)):
            rule = dict(r)
            rule["conditions"] = [dict(c) for c in db.execute(
                "SELECT * FROM parser_rule_conditions WHERE rule_id=? "
                "ORDER BY id", (r["id"],))]
            rules.append(rule)
        rules_by_profile[p["id"]] = rules

    # (profile_id, title, источник) для всех названий, привязанных к профилям
    inputs = []
    for row in db.execute(
            "SELECT s.parser_profile_id pid, m.source_title t FROM media_items m "
            "JOIN series s ON s.id = m.series_id "
            "WHERE m.source_title IS NOT NULL AND s.parser_profile_id IS NOT NULL"):
        inputs.append((row["pid"], row["t"], "vk"))
    for row in db.execute(
            "SELECT s.parser_profile_id pid, tf.original_path t FROM torrent_files tf "
            "JOIN torrents tr ON tr.id = tf.torrent_db_id "
            "JOIN series s ON s.id = tr.series_id "
            "WHERE s.parser_profile_id IS NOT NULL"):
        inputs.append((row["pid"], row["t"], "torrent"))
    return rules_by_profile, inputs


def essence_old(old_result: dict) -> dict:
    events = old_result.get("match_events", [])
    excluded = any(e.get("action") == "exclude" for e in events)
    return {"excluded": excluded,
            "extracted": {} if excluded else old_result["result"]["extracted"]}


def essence_new(new_result: dict) -> dict:
    return {"excluded": new_result["excluded"],
            "extracted": new_result["extracted"]}


def main() -> None:
    rules_by_profile, inputs = load_fixture()
    old_engine = load_old_engine(rules_by_profile)
    new_profiles = {pid: compile_profile(pid, rules)
                    for pid, rules in rules_by_profile.items()}

    same, diffs = 0, []
    for pid, title, source in inputs:
        old = essence_old(old_engine.process_videos(pid, [{"title": title}])[0])
        new = essence_new(process_title(new_profiles[pid], title))
        if old == new:
            same += 1
        else:
            diffs.append({"profile": pid, "source": source, "title": title,
                          "old": old, "new": new})

    print(f"входов: {len(inputs)}; совпало: {same}; расхождений: {len(diffs)}")
    for d in diffs:
        print(f"\nпрофиль {d['profile']} [{d['source']}] {d['title']!r}")
        print(f"  старый: {d['old']}")
        print(f"  новый:  {d['new']}")
    if diffs:
        out = "tests/rules_diff_report.json"
        json.dump(diffs, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\nполный отчёт: {out}")


if __name__ == "__main__":
    main()
