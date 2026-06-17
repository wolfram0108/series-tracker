"""Golden-харнесс контракта HTTP: фиксирует СТРУКТУРУ ответов (форму),
не значения. Назначение — доказать, что добавление response_model к
маршрутам gateway не меняет форму JSON (правило «бэкенд только additive»,
ТЗ frontend-rewrite §9).

Сравнивается «скелет» ответа: листья заменяются на имя типа, у списков
объектов ключи объединяются. Это ловит исчезновение/добавление полей и
смену типа (именно это делает response_model при отсечении «лишнего»),
но игнорирует волатильные значения (таймстампы, прогресс, dlspeed).

Использование (сервер должен быть запущен на :5000):
    .venv/bin/python tests/api_golden.py capture   # снять эталон (до правок)
    .venv/bin/python tests/api_golden.py check      # сверить (после правок)

Снимки — в tests/golden/api/<slug>.json (вне git: только read-only GET,
но на всякий случай данные стенда не коммитим — см. .gitignore).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:5000"
GOLDEN_DIR = Path(__file__).parent / "golden" / "api"
DB = Path(__file__).parent.parent / "app.db"


def _ids() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        series = [r[0] for r in con.execute("SELECT id FROM series ORDER BY id")]
        profiles = [r[0] for r in con.execute(
            "SELECT id FROM parser_profiles ORDER BY id")]
    finally:
        con.close()
    return {"series": series, "profile": profiles[0] if profiles else None}


def _endpoints() -> list[tuple[str, str]]:
    ids = _ids()
    s = ids["series"][0] if ids["series"] else 1
    s2 = ids["series"][1] if len(ids["series"]) > 1 else s
    eps: list[tuple[str, str]] = [
        ("series_list", "/api/series"),
        (f"series_get_{s}", f"/api/series/{s}"),
        (f"series_get_{s2}", f"/api/series/{s2}"),
        (f"series_history_{s}", f"/api/series/{s}/torrents/history"),
        ("active_torrents", "/api/series/active_torrents"),
        ("scanner_status", "/api/scanner/status"),
        ("agent_queue", "/api/agent/queue"),
        ("downloads_queue", "/api/downloads/queue"),
        (f"media_items_{s}", f"/api/series/{s}/media-items"),
        (f"media_items_{s2}", f"/api/series/{s2}/media-items"),
        (f"composition_{s}", f"/api/series/{s}/composition"),
        (f"rename_preview_{s}", f"/api/series/{s}/rename_preview"),
        (f"sliced_files_{s}", f"/api/series/{s}/sliced-files"),
        (f"source_filenames_{s}", f"/api/series/{s}/source-filenames"),
        ("auth", "/api/auth"),
        ("directories", "/api/directories?path=/"),
        ("trackers", "/api/trackers"),
        ("parser_profiles", "/api/parser-profiles"),
        ("logs", "/api/logs?limit=5"),
        ("database_tables", "/api/database/tables"),
        ("database_table_series", "/api/database/table/series"),
        ("set_force_replace", "/api/settings/force_replace"),
        ("set_less_strict", "/api/settings/less_strict_scan"),
        ("set_parallel", "/api/settings/parallel_downloads"),
        ("set_concurrent", "/api/settings/concurrent_fragments"),
        ("set_slicing_delete", "/api/settings/slicing_delete_source"),
        ("set_saved_paths", "/api/settings/saved_paths"),
        ("set_debug_flags", "/api/settings/debug_flags"),
    ]
    if ids["profile"] is not None:
        eps.append((f"profile_rules_{ids['profile']}",
                    f"/api/parser-profiles/{ids['profile']}/rules"))
    return eps


def _skeleton(v):
    """Структура: листья → имя типа; список объектов → union ключей."""
    if isinstance(v, dict):
        return {k: _skeleton(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        if not v:
            return ["<empty-list>"]
        if all(isinstance(e, dict) for e in v):
            keys: dict = {}
            for e in v:
                for k in e:
                    keys[k] = _skeleton(e[k])
            return ["<list-of-objects>", {k: keys[k] for k in sorted(keys)}]
        return ["<list>", _skeleton(v[0])]
    return type(v).__name__


def _fetch(path: str) -> dict:
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    try:
        payload = json.loads(body)
    except Exception:
        payload = {"<non-json>": body[:200].decode("utf-8", "replace")}
    return {"status": status, "shape": _skeleton(payload)}


def capture() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for slug, path in _endpoints():
        snap = _fetch(path)
        (GOLDEN_DIR / f"{slug}.json").write_text(
            json.dumps({"path": path, **snap}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"  сняли {slug} ({snap['status']}) ← {path}")
    print(f"эталон сохранён: {GOLDEN_DIR}")


def check() -> int:
    mismatches = 0
    for slug, path in _endpoints():
        gold_file = GOLDEN_DIR / f"{slug}.json"
        if not gold_file.exists():
            print(f"  НЕТ ЭТАЛОНА: {slug}")
            mismatches += 1
            continue
        gold = json.loads(gold_file.read_text(encoding="utf-8"))
        cur = _fetch(path)
        if cur["status"] != gold["status"] or cur["shape"] != gold["shape"]:
            mismatches += 1
            print(f"  ДИФФ: {slug} ({path})")
            print(f"    эталон: status={gold['status']}")
            print(f"    сейчас: status={cur['status']}")
            if cur["shape"] != gold["shape"]:
                print(f"    форма ответа изменилась:")
                print(f"      эталон: {json.dumps(gold['shape'], ensure_ascii=False)}")
                print(f"      сейчас: {json.dumps(cur['shape'], ensure_ascii=False)}")
        else:
            print(f"  ok {slug}")
    if mismatches:
        print(f"\nРАСХОЖДЕНИЙ: {mismatches} — форма контракта изменилась!")
        return 1
    print("\nвсё совпало — форма ответов не изменилась (additive подтверждён)")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "capture":
        capture()
    elif mode == "check":
        sys.exit(check())
    else:
        print("использование: api_golden.py [capture|check]")
        sys.exit(2)
