"""The registry of the lines of a video application, made of the records of their admission
(tools/collectors/app_lines_admission.py, `data/raw/app_lines_admission__*__<app>-*.jsonl`).

A line is admitted when the last record of each of its first, central and last episode says its file came whole and was
read back; any other line is refused, with the reason of its first episode that did not come. The registry holds no
address of a stream: the episodes, their frames, sound, length, size and the speed of their download.

    .venv/bin/python tools/analyzers/app_lines_registry.py <app>   →   modules/sources/apps/admitted/<app>.json
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WHICH = ("first", "central", "last")


def registry(app: str) -> dict:
    last: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", f"app_lines_admission__*__{app}-*.jsonl"))):
        with open(path, encoding="utf-8") as raw:
            for line in raw:
                record = json.loads(line)
                last[(record["line"], record["which"])] = record
    admitted, refused = {}, {}
    for code in sorted({line for line, _which in last}):
        records = [last.get((code, which)) for which in WHICH]
        if all(record is not None and record["ok"] for record in records):
            admitted[code] = {"card": records[0]["card"], "at": max(record["at"] for record in records), "episodes": [
                {"which": record["which"], "label": record["label"], "width": record["file"]["width"],
                 "height": record["file"]["height"], "audio": record["file"]["audio"],
                 "duration": record["file"]["duration"], "size": record["size"], "mbit_s": record.get("mbit_s")}
                for record in records]}
        else:
            failed = next(record for record in records if record is None or not record["ok"])
            refused[code] = {"card": records[0]["card"] if records[0] else None,
                             "episode": failed["label"] if failed else None,
                             "step": failed.get("step") if failed else "not asked",
                             "reason": (failed.get("reason") or "")[:300] if failed else "not asked"}
    return {"app": app, "admitted": admitted, "refused": refused}


if __name__ == "__main__":
    app = sys.argv[1]
    made = registry(app)
    target = os.path.join(ROOT, "modules", "sources", "apps", "admitted", f"{app}.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as out:
        json.dump(made, out, ensure_ascii=False, indent=1)
        out.write("\n")
    print("admitted:", ", ".join(made["admitted"]), "| refused:", ", ".join(made["refused"]))
    print("REGISTRY_DONE")
