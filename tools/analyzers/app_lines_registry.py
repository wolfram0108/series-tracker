"""The registry of the lines of a video application, made of the records of their admission
(tools/collectors/app_lines_admission.py, `data/raw/app_lines_admission__*__<app>-*.jsonl`).

A line is admitted when the last record of each of its first, central and last episode says its file came whole and was
read back, and the last read of the last ten episodes of it by the service's own code
(`data/raw/app_table_rate__*__<app>-*.jsonl`, the reads of `recent` 10) gave every row of it the frame of its episode, and the three files are three files (one clip served for every episode is no
episode); any other line is refused, with the reason of the episode or the rows that failed. The registry holds no
address of a stream: the episodes, their frames, sound, length, size and the speed of their download.

    .venv/bin/python tools/analyzers/app_lines_registry.py <app>   →   modules/sources/apps/admitted/<app>.json
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WHICH = ("first", "central", "last")
# The table part of the admission is the read of the last episodes of each line (requirement 4а): a read of a whole card,
# made for another measure, is no part of it (old episodes of an admitted line may be gone from their host).
ADMISSION_RECENT = 10


def table_misses(app: str) -> dict[str, list[dict]]:
    """The rows of each line left without the frame of their episode in the last read of its card by the service's own
    code (tools/collectors/app_table_rate.py): a line whose rows the table cannot measure is no player."""
    last: dict[str, list[dict]] = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", f"app_table_rate__*__{app}-*.jsonl"))):
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        if not any(row.get("recent") == ADMISSION_RECENT for row in rows if "rows_per_s" in row):
            continue
        read = [row for row in rows if "heights" in row]
        for code in {row["player"].removeprefix("line:") for row in read}:
            last[code] = [row for row in read if row["player"] == f"line:{code}"]
    return {code: [row for row in rows if not row["heights"]] for code, rows in last.items()
            if any(not row["heights"] for row in rows)}


def same_file(records: list[dict]) -> bool:
    """The three files of a line are one and the same: the same bytes and the same length for different episodes. A
    parser that serves one clip for every episode (a notice for those who did not pay) gives no episode."""
    return len({(record.get("size"), (record.get("file") or {}).get("duration")) for record in records}) < len(records)


def public(text: str) -> str:
    """A reason as the registry keeps it (the registry is in git): no address of a machine in it — a host that refuses
    by the address of the asker names the address of the server of the service."""
    return re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)


def registry(app: str) -> dict:
    misses = table_misses(app)
    last: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", f"app_lines_admission__*__{app}-*.jsonl"))):
        with open(path, encoding="utf-8") as raw:
            for line in raw:
                record = json.loads(line)
                last[(record["line"], record["which"])] = record
    admitted, refused = {}, {}
    for code in sorted({line for line, _which in last}):
        records = [last.get((code, which)) for which in WHICH]
        if all(record is not None and record["ok"] for record in records) and same_file(records):
            refused[code] = {"card": records[0]["card"], "episode": records[0]["label"], "step": "file",
                             "reason": f"one and the same file for the first, the central and the last episode "
                                       f"({records[0]['size']} bytes, {records[0]['file']['duration']} s): not the episodes"}
        elif all(record is not None and record["ok"] for record in records) and code in misses:
            rows = misses[code]
            refused[code] = {"card": records[0]["card"], "episode": rows[0]["text"], "step": "table",
                             "reason": public(f"{len(rows)} rows of the table without the frame of their episode, as "
                                              f"{rows[0]['fields'].get('frame_failed', '')}")[:300]}
        elif all(record is not None and record["ok"] for record in records):
            admitted[code] = {"card": records[0]["card"], "at": max(record["at"] for record in records), "episodes": [
                {"which": record["which"], "label": record["label"], "width": record["file"]["width"],
                 "height": record["file"]["height"], "audio": record["file"]["audio"],
                 "duration": record["file"]["duration"], "size": record["size"], "mbit_s": record.get("mbit_s")}
                for record in records]}
        else:
            # The reason is the episode that failed; an episode never asked (its line was decided first) tells none.
            failed = next((record for record in records if record is not None and not record["ok"]), None)
            refused[code] = {"card": next((record["card"] for record in records if record), None),
                             "episode": failed["label"] if failed else None,
                             "step": failed.get("step") if failed else "not asked",
                             "reason": public(failed.get("reason") or "")[:300] if failed else "not asked"}
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
