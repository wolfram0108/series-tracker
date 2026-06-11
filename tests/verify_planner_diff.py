"""Дифф старого SmartCollector и нового планировщика на реальных данных.

Запуск:  .venv/bin/python tests/verify_planner_diff.py

Все media_items каждого VK-сериала фикстуры подаются обоим алгоритмам
как «кандидаты» (одинаковый вход — корректное сравнение выходов).

ОЖИДАЕМЫЙ РЕЗУЛЬТАТ: 9/10 совпадений + 1 согласованное отклонение
(сериал 87, фикс Г/находка 22: лучшая одиночка теперь действительно
лучшая — старый код выбирал 1080p вместо 2160p из-за инверсии max/min).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLD_ROOT = "/home/user/series-tracker"

from modules.scan.planner import build_plan  # noqa: E402


class _ShimLogger:
    def info(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass


class _ShimDb:
    """Подсовывает старому SmartCollector кандидатов и ловит результат."""
    def __init__(self, series: dict, candidates: list[dict]):
        self._series = series
        self._candidates = candidates
        self.captured: dict[str, str] | None = None

    def get_series(self, series_id):
        return self._series

    def get_media_items_by_plan_status(self, series_id, status):
        return [dict(c) for c in self._candidates]

    def update_media_item_plan_statuses(self, statuses):
        self.captured = dict(statuses)


def load_old_collector_class():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "old_smart_collector", os.path.join(OLD_ROOT, "smart_collector.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SmartCollector


def main() -> None:
    db = sqlite3.connect("tests/fixtures/app.fixture.db")
    db.row_factory = sqlite3.Row
    SmartCollector = load_old_collector_class()

    total_series = same = 0
    diffs = []
    for s in db.execute("SELECT id, vk_quality_priority FROM series "
                        "WHERE source_type='vk_video'"):
        candidates = [dict(r) for r in db.execute(
            "SELECT unique_id, season, episode_start, episode_end, resolution "
            "FROM media_items WHERE series_id=?", (s["id"],))]
        if not candidates:
            continue
        total_series += 1

        series_row = {"vk_quality_priority": s["vk_quality_priority"]}
        shim = _ShimDb(series_row, candidates)
        SmartCollector(_ShimLogger(), shim).collect(s["id"])
        old = shim.captured or {}

        priority = []
        try:
            priority = json.loads(s["vk_quality_priority"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass
        new = build_plan(candidates, priority)

        if old == new:
            same += 1
        else:
            changed = {k: (old.get(k), new.get(k))
                       for k in set(old) | set(new) if old.get(k) != new.get(k)}
            diffs.append({"series_id": s["id"], "items": len(candidates),
                          "diff": changed})

    print(f"VK-сериалов: {total_series}; совпало планов: {same}; "
          f"расхождений: {len(diffs)}")
    for d in diffs:
        print(f"\nсериал {d['series_id']} ({d['items']} элементов):")
        for uid, (o, n) in list(d["diff"].items())[:10]:
            print(f"  {uid}: старый={o} новый={n}")


if __name__ == "__main__":
    main()
