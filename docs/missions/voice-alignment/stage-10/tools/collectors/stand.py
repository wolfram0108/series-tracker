"""The stand of stage 10 and its live measurements: an isolated instance of the worktree on a free port.

Raw results go to `raw/measurements/<id>__<date>.jsonl` (kept out of git); the last line printed in any outcome is
`ИЗМЕРЕНИЕ <id>: PASS ...` or `ИЗМЕРЕНИЕ <id>: FAIL ...`, which is what the runner of runs waits for. A run of this tool
is a run in the sense of the law of runs: start it only through `progon_runner.py` with that line as the predicate.

    stand.py table --id M-7 --url <address> [--option k=v ...] [--credentials-from <app.db>] [--safeguard 170]

`table` reads the page of an address through the stand (the one read per page, the events of the progress) and reports
the time, the state, the players, rows, groups and resolutions of its summary, and how many rows the constructor gets.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[6]
RAW = Path(__file__).resolve().parents[2] / "raw" / "measurements"
PY = sys.executable


class Stand:
    """An instance on a free port with a new database; leaves nothing behind but the journal in `raw/`."""

    def __init__(self, credentials_from: str | None = None, whole_db_from: str | None = None,
                 profiles: list[dict] | None = None, work: str | None = None) -> None:
        self.whole_db_from = whole_db_from
        self.profiles = profiles or []
        # A named work folder outlives the stand: the next one finds the database where the last left it.
        self.kept = work is not None
        if whole_db_from and not credentials_from:
            credentials_from = whole_db_from
        self.work = Path(work) if work else Path(tempfile.mkdtemp(prefix="stage10-stand-"))
        self.work.mkdir(parents=True, exist_ok=True)
        self.db = self.work / "app.db"
        self.fresh = not self.db.exists()
        self.credentials_from = credentials_from
        self.proc: subprocess.Popen | None = None
        self.base = ""
        self.journal = self.work / "stand.log"

    def __enter__(self) -> "Stand":
        env = {**os.environ, "ST_DB_PATH": str(self.db), "ST_DB_URL": f"sqlite:///{self.db}", "ST_AUTH_REQUIRED": "false",
               "ST_COOKIE_SECURE": "false", "ST_SECRET_KEY": "stage10", "ST_LOG_DIR": str(self.work)}
        for name in ("ST_QBIT_URL", "ST_CONFORM_URL"):
            env.pop(name, None)
        if self.credentials_from:
            # Copied secrets are readable only with the keys they were written under; the values stay in the process.
            env.update(self._keys_of_the_source())
        if self.whole_db_from:
            self._copy_whole_db()
        migrated = subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True)
        if migrated.returncode != 0:
            raise RuntimeError(f"migrations failed: {migrated.stderr[-300:]}")
        if self.credentials_from and not self.whole_db_from:
            self._copy_credentials()
        if self.profiles and self.fresh:
            self._seed_profiles()
        if self.whole_db_from:
            self._silence_the_schedule()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.proc = subprocess.Popen([PY, "-m", "uvicorn", "run:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = threading.Event()

        def pump() -> None:
            with open(self.journal, "a") as out:
                for line in self.proc.stdout:
                    out.write(line)
                    out.flush()
                    if "Application startup complete" in line:
                        ready.set()

        threading.Thread(target=pump, daemon=True).start()
        if not ready.wait(90):
            self.__exit__(None, None, None)
            raise RuntimeError("the stand did not come up in 90 s")
        self.base = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *_exc) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        RAW.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
        if self.journal.exists():
            shutil.copy(self.journal, RAW / f"stand__{stamp}.log")
        # The logs of the modules, one file per group: what a failed step says is there and nowhere else.
        for path in self.work.glob("*.log"):
            if path != self.journal:
                shutil.copy(path, RAW / f"stand__{stamp}__{path.name}")
        if not self.kept:
            shutil.rmtree(self.work, ignore_errors=True)

    def _keys_of_the_source(self) -> dict[str, str]:
        """ST_ENCRYPTION_KEY and ST_SECRET_KEY of the `.env` that stands next to the database the rows come from."""
        found: dict[str, str] = {}
        env_file = Path(self.credentials_from).resolve().parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                if name.strip() in ("ST_ENCRYPTION_KEY", "ST_SECRET_KEY") and value.strip():
                    found[name.strip()] = value.strip().strip('"').strip("'")
        return found

    def _copy_whole_db(self) -> None:
        """A copy of a real database by the backup API of SQLite; the source is opened read-only and never written."""
        source = sqlite3.connect(f"file:{self.whole_db_from}?mode=ro", uri=True)
        target = sqlite3.connect(self.db)
        source.backup(target)
        target.close()
        source.close()

    def _silence_the_schedule(self) -> None:
        """The copy must not scan, fetch or download anything of its own: the schedule of the scanner is switched off
        and no series is left on the automatic scan, before the instance comes up."""
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE series SET auto_scan_enabled=0")
        conn.execute("UPDATE settings SET value='false' WHERE key='scanner_agent_enabled'")
        conn.commit()
        conn.close()

    def _seed_profiles(self) -> None:
        """Parser profiles written straight into the new database: {"name", "rules": [{"name", "conditions": [blocks],
        "actions": [{"action_type", "action_pattern"}]}]}, a rule per entry in the order given."""
        target = sqlite3.connect(self.db)
        for profile in self.profiles:
            profile_id = target.execute("INSERT INTO parser_profiles (name) VALUES (?)", (profile["name"],)).lastrowid
            for priority, rule in enumerate(profile["rules"]):
                rule_id = target.execute(
                    "INSERT INTO parser_rules (profile_id, name, priority, action_pattern, continue_after_match) "
                    "VALUES (?, ?, ?, ?, 0)", (profile_id, rule["name"], priority, json.dumps(rule["actions"]))).lastrowid
                for blocks in rule["conditions"]:
                    target.execute(
                        "INSERT INTO parser_rule_conditions (rule_id, condition_type, pattern, logical_operator) "
                        "VALUES (?, 'contains', ?, 'AND')", (rule_id, json.dumps(blocks)))
        target.commit()
        target.close()

    def _copy_credentials(self) -> None:
        """The token of VK lives in the table `auth` (module trackerauth); the stand takes a copy of the rows."""
        source = sqlite3.connect(f"file:{self.credentials_from}?mode=ro", uri=True)
        target = sqlite3.connect(self.db)
        for table in ("auth", "tracker_sessions"):
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
            rows = source.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            target.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                               rows)
        target.commit()

    def listen(self, names: set[str], match, safeguard: float):
        """Collect the events of the stream until `match(name, data)` is true or the safeguard ends; returns a handle."""
        found: list[tuple[str, dict]] = []
        done = threading.Event()
        connected = threading.Event()
        deadline = time.monotonic() + safeguard

        def run() -> None:
            try:
                with httpx.stream("GET", f"{self.base}/api/stream", timeout=httpx.Timeout(10, read=safeguard)) as stream:
                    connected.set()
                    name = None
                    for line in stream.iter_lines():
                        if time.monotonic() > deadline:
                            break
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:") and name in names:
                            data = json.loads(line[5:].strip() or "null")
                            found.append((name, data))
                            if match(name, data):
                                break
            except httpx.HTTPError:
                pass
            finally:
                done.set()

        threading.Thread(target=run, daemon=True).start()
        connected.wait(15)
        return found, done


def measure_table(stand: Stand, url: str, options: dict[str, str], safeguard: float) -> dict:
    """One read of one page: the time to the signal of its end, then what the summary and the lines say."""
    names = {"sources_table_progress", "sources_table_ready", "sources_table_failed"}
    events, done = stand.listen(names, lambda name, _d: name in ("sources_table_ready", "sources_table_failed"), safeguard)
    began = time.monotonic()
    with httpx.Client(base_url=stand.base, timeout=60) as client:
        first = client.post("/api/sources/preview", json={"url": url, "options": options, "refresh": True}).json()
        if first.get("state") != "ready":
            done.wait(safeguard)
        elapsed = time.monotonic() - began
        final = client.post("/api/sources/preview", json={"url": url, "options": options, "refresh": False}).json()
    outcome = [name for name, _ in events if name != "sources_table_progress"]
    summary = final.get("summary") or {}
    players = summary.get("players") or []
    return {
        "url": url, "options": options, "elapsed_s": round(elapsed, 1), "state": final.get("state"),
        "reason": final.get("reason"), "ended_by": outcome[-1] if outcome else None,
        "progress_events": sum(1 for name, _ in events if name == "sources_table_progress"),
        "names": final.get("names"), "lines": len(final.get("lines") or []),
        "players": [{"player": p["player"], "rows": p["rows"], "groups": p["groups"], "heights": p["heights"],
                     "voices": [v["name"] for v in p.get("voices", [])]} for p in players],
    }


def check_series(stand: Stand) -> dict:
    """M-20 on a migrated copy: every series is listed and opens with its kind and its sources, and a video series
    is deleted by the ordinary request, which takes its records and nothing else."""
    with httpx.Client(base_url=stand.base, timeout=60) as client:
        listed = client.get("/api/series").json()
        opened, broken = 0, []
        for item in listed:
            card = client.get(f"/api/series/{item['id']}")
            if card.status_code != 200 or "kind" not in card.json() or not card.json().get("sources"):
                broken.append(item["id"])
            else:
                opened += 1
        kinds: dict[str, int] = {}
        for item in listed:
            kinds[item.get("kind")] = kinds.get(item.get("kind"), 0) + 1
        victim = next((item["id"] for item in listed if item.get("kind") == "video"), None)
        deleted = None
        if victim is not None:
            deleted = client.delete(f"/api/series/{victim}").status_code
        after = len(client.get("/api/series").json())
    return {"series": len(listed), "opened": opened, "broken": broken, "kinds": kinds, "deleted_status": deleted,
            "listed_after_delete": after}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="what", required=True)
    table = sub.add_parser("table")
    table.add_argument("--id", required=True)
    table.add_argument("--url", required=True)
    table.add_argument("--option", action="append", default=[])
    table.add_argument("--credentials-from")
    table.add_argument("--safeguard", type=float, default=170)
    listing = sub.add_parser("series")
    listing.add_argument("--id", required=True)
    listing.add_argument("--from-db", required=True)
    args = parser.parse_args()
    if args.what == "series":
        try:
            with Stand(whole_db_from=args.from_db) as stand:
                record = check_series(stand)
        except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
            print(f"ИЗМЕРЕНИЕ {args.id}: FAIL прибор: {type(exc).__name__}: {exc}")
            return 2
        RAW.mkdir(parents=True, exist_ok=True)
        with open(RAW / f"{args.id}__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False))
        ok = (record["series"] > 0 and record["opened"] == record["series"] and record["deleted_status"] == 200
              and record["listed_after_delete"] == record["series"] - 1)
        print(f"ИЗМЕРЕНИЕ {args.id}: {'PASS' if ok else 'FAIL'} series={record['series']} opened={record['opened']} "
              f"broken={record['broken']} deleted={record['deleted_status']}")
        return 0 if ok else 1
    options = dict(pair.split("=", 1) for pair in args.option)
    try:
        with Stand(args.credentials_from) as stand:
            record = measure_table(stand, args.url, options, args.safeguard)
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        print(f"ИЗМЕРЕНИЕ {args.id}: FAIL прибор: {type(exc).__name__}: {exc}")
        return 2
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"{args.id}__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
    rows = sum(p["rows"] for p in record["players"])
    print(json.dumps(record, ensure_ascii=False)[:1500])
    ok = record["state"] == "ready" and rows > 0
    print(f"ИЗМЕРЕНИЕ {args.id}: {'PASS' if ok else 'FAIL'} state={record['state']} rows={rows} lines={record['lines']} "
          f"elapsed={record['elapsed_s']}s reason={record['reason']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
