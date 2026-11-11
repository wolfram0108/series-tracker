"""The analyzers of the tools (tools/analyzers) on sample records with answers known beforehand: the decisions of each
(what a line is admitted by, what a dump says of a sync) and its whole path from the raw records to its output."""
from __future__ import annotations

import gzip
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", "analyzers", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lines(path, records) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- the registry of the lines of an application --------------------------------------------------------------------

def episode(line: str, which: str, ok: bool = True, **more) -> dict:
    return {"app": "app", "card": "7", "line": line, "which": which, "label": which, "ok": ok, "at": f"2026-01-01T00:0{len(which)}",
            "size": 100, "mbit_s": 50.0, "file": {"width": 1920, "height": 1080, "audio": 1, "duration": "60.0"}, **more}


def table(rows: list[tuple[str, str, list[int]]], recent: int | None) -> list[dict]:
    return [*({"app": "app", "card": "7", "player": f"line:{line}", "text": text, "heights": heights,
               "fields": {} if heights else {"frame_failed": "the host answered 404"}} for line, text, heights in rows),
            {"app": "app", "card": "7", "recent": recent, "rows": len(rows), "rows_per_s": 1.0}]


def test_a_line_is_admitted_by_three_whole_episodes_and_the_frames_of_its_last_ten_rows(tmp_path, monkeypatch):
    made = tool("app_lines_registry")
    monkeypatch.setattr(made, "ROOT", str(tmp_path))
    raw = tmp_path / "data" / "raw"
    lines(raw / "app_lines_admission__1__app-7.jsonl", [
        *(episode("whole", which) for which in made.WHICH),
        *(episode("blind", which) for which in made.WHICH),
        *(episode("old", which) for which in made.WHICH),
        episode("broken", "first"), episode("broken", "central", ok=False, step="download", reason="обрыв"),
        episode("again", "first", ok=False, step="stream", reason="the host answered 403"),
    ])
    # A later record of an episode is the one that counts: the line asked again came whole.
    lines(raw / "app_lines_admission__2__app-7.jsonl", [episode("again", which) for which in made.WHICH])
    lines(raw / "app_table_rate__1__app-7.jsonl", table(
        [("whole", "9", [1080]), ("blind", "9", []), ("blind", "10", [720]), ("old", "9", [720]), ("again", "9", [720])], 10))
    # A read of the whole card is no part of the admission: an old episode of an admitted line gone from its host.
    lines(raw / "app_table_rate__2__app-7.jsonl", table([("old", "1", []), ("whole", "1", [])], None))

    registry = made.registry("app")
    assert sorted(registry["admitted"]) == ["again", "old", "whole"]
    assert registry["admitted"]["whole"]["episodes"][0] == {"which": "first", "label": "first", "width": 1920, "height": 1080,
                                                           "audio": 1, "duration": "60.0", "size": 100, "mbit_s": 50.0}
    assert registry["refused"]["blind"]["step"] == "table" and "1 rows of the table" in registry["refused"]["blind"]["reason"]
    assert registry["refused"]["broken"] == {"card": "7", "episode": "central", "step": "download", "reason": "обрыв"}


# --- the dump of a sync -----------------------------------------------------------------------------------------------

def bus(at: str, topic: str, payload: dict) -> dict:
    return {"t": f"2026-01-01T00:{at}+00:00", "e": "bus", "kind": "event", "topic": topic, "payload": payload}


def trace(at: str, kind: str, data: dict) -> dict:
    return bus(at, "planner.sync.trace", {"series_id": 1, "run": "r", "kind": kind, "download": True, "data": data})


def test_the_dump_of_a_sync_is_told_step_by_step_with_the_times_the_speeds_and_the_wait_of_each_group(tmp_path, capsys):
    made = tool("run_dump")
    row = "row:RowId(source_id=7, row_key='line:1', video='v')"
    goal = {"slot": [1, 1], "video": {"kind": "download", "snapshot": {"source": "app", "player": "line:x", "height": 2160}},
            "tracks": [{"voice": "G1", "snapshot": {"player": "kodik"}}]}
    task = {"id": "1:s01e01:video:", "stages": [{"name": "download", "seconds": 10.0}, {"name": "remux", "seconds": 2.0}],
            "figures": {"size_mb": 95.0, "done_mb": 100.0, "source_seconds": 60.0, "got_seconds": 60.0},
            "log": [{"kind": "attempt"}]}
    records = [
        {"t": "2026-01-01T00:00:00+00:00", "e": "open"},
        bus("00:01", "sources.table.progress", {"url": "https://site/a"}),
        bus("00:05", "sources.table.ready", {"url": "https://site/a"}),
        bus("00:05", "sources.table.ready", {"url": "app://app/7"}),
        trace("00:06", "plan", {"round": 1, "inputs": {"online": [{}, {}], "containers": [], "failed": []}, "plan": {"goals": [goal]}}),
        trace("00:06", "step", {"op": row, "kind": "download_video", "slot": [1, 1], "ready": True}),
        bus("00:06", "downloads.fetch", {"key": "k", "need": {"key": "1:s01e01:video:", "episode": "s01e01", "kind": "video"},
                                         "variant": {"place": "app", "player_label": "x", "height": 2160}}),
        bus("00:06", "downloads.fetch.started", {"key": "k"}),
        bus("00:18", "downloads.queue.changed", {"tasks": [task]}),
        bus("00:18", "downloads.fetch.finished", {"key": "k", "ok": True, "error": "", "path": "/nowhere/file.mp4"}),
        trace("00:18", "done", {"op": row, "outcome": "made"}),
        trace("00:20", "replan", {"recorded": [["download", "x"]], "for_this_sync": [], "retimed": []}),
        trace("00:20", "plan", {"round": 2, "inputs": {"online": [], "containers": [{}], "failed": [1]}, "plan": {"goals": [goal]}}),
        bus("00:30", "conform.job.finished", {"job_id": "j1", "status": "done", "error": None, "results": [
            {"dub": "e01 G1.mp4", "ok": True, "suspect": True, "warnings": ["сопоставлено лишь 74% кадров"], "resid_ms": 1.5}]}),
        trace("00:48", "apply", {"slots": [[1, 1]], "applied": True, "group": {"ops": [row]}}),
        {"t": "2026-01-01T00:00:50+00:00", "e": "log", "level": "WARNING", "group": "planner", "message": "сборка не удалась"},
        {"t": "2026-01-01T00:00:51+00:00", "e": "log", "level": "INFO", "group": "planner", "message": "конец"},
    ]
    path = tmp_path / "dump.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    made.main(str(path), False)
    told = capsys.readouterr().out
    assert "00:00:00 — 00:00:51 UTC, 51 с, строк 17" in told
    assert "https://site/a — читалась 4 с" in told and "app://app/7 — из базы" in told
    assert "план, круг 1 (00:00:06): онлайн-строк 2, файлов в активе 0, неудач 0, целей 1" in told
    assert "s01e01: видео download app line:x 2160p; озвучки: G1←kodik" in told
    # A plan made again says what made it, keeps the count of the rows of the first one and shows no episode it left as it was.
    assert "план составляется заново (00:00:20): записано неудач 1" in told
    assert "план, круг 2 (00:00:20): онлайн-строк 2, файлов в активе 1, неудач 1, целей 1" in told
    assert told.count("s01e01: видео download") == 1
    assert "s01e01 download_video  00:00:06→00:00:18     12 с  made    7:line:1" in told
    # The speed is of what was downloaded in the time of its download: 100 MB in 10 s.
    assert "95.0 МБ   80.0 Мбит/с  download 10 remux 2  длина 60.0 из 60.0 с  ok" in told
    assert "e01 G1.mp4" in told and "подозрение остаток 1.5 мс  сопоставлено лишь 74% кадров" in told
    assert "00:00:48 s01e01: применена ждала после последнего шага 30 с" in told
    assert "WARNING planner: сборка не удалась" in told and "конец" not in told
