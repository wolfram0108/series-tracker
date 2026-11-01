"""M-32: the migrations of the stage on a copy of a real database, up to the head and back to the revision before them.

    migrate_copy.py --db /path/to/app.db [--before 0024] [--id M-32]

The source is opened read-only (the copy is taken by the backup API of SQLite) and is never written. On the copy the
tool counts the rows of the tables the stage touches, migrates to the head, checks the integrity and the foreign keys
and the rules of the data (one source for every series, no video series without the columns it needs), migrates back and
checks that the rows came back. The last line printed in any outcome is `ИЗМЕРЕНИЕ <id>: PASS ...` or `FAIL ...`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
PY = sys.executable
COUNTED = ("series", "media_items", "voice_dubs", "parser_profiles", "parser_rules", "parser_rule_conditions",
           "download_tasks", "torrents", "torrent_files", "scan_tasks")


def counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        found = {}
        for table in COUNTED:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            found[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if exists else -1
        return found
    finally:
        conn.close()


def alembic(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ST_DB_URL": f"sqlite:///{path}"}
    return subprocess.run([PY, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def revision_of(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--before", default="0024")
    parser.add_argument("--id", default="M-32")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="stage10-migrate-"))
    problems: list[str] = []
    try:
        copy = work / "copy.db"
        source = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        source.backup(target)
        target.close()
        source.close()
        start_rev = revision_of(copy)
        was = counts(copy)

        up = alembic(copy, "upgrade", "head")
        if up.returncode != 0:
            print(f"ИЗМЕРЕНИЕ {args.id}: FAIL upgrade: {up.stderr.strip()[-400:]}")
            return 1
        head = revision_of(copy)
        conn = sqlite3.connect(copy)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=ON")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        series = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        sources = conn.execute("SELECT COUNT(*) FROM series_sources").fetchone()[0]
        per_series = conn.execute("SELECT COUNT(*) FROM (SELECT series_id FROM series_sources GROUP BY series_id "
                                  "HAVING COUNT(*) != 1)").fetchone()[0]
        types = dict(conn.execute("SELECT source_type, COUNT(*) FROM series_sources GROUP BY source_type").fetchall())
        gone = [name for name in ("video_availability",) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()]
        conn.close()
        up_counts = counts(copy)
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
        if broken:
            problems.append(f"foreign_key_check: {len(broken)} rows")
        if series != sources or per_series:
            problems.append(f"sources: {sources} for {series} series, {per_series} series without exactly one")
        if gone:
            problems.append(f"tables that must be gone remain: {gone}")
        for table in ("series", "media_items", "voice_dubs", "parser_profiles", "parser_rules", "parser_rule_conditions"):
            if was[table] != up_counts[table]:
                problems.append(f"{table}: {was[table]} rows before, {up_counts[table]} after the upgrade")

        down = alembic(copy, "downgrade", args.before)
        if down.returncode != 0:
            problems.append(f"downgrade to {args.before}: {down.stderr.strip()[-300:]}")
            back = {}
        else:
            back = counts(copy)
            for table in ("series", "media_items", "parser_profiles", "parser_rules", "parser_rule_conditions"):
                if was[table] != back[table]:
                    problems.append(f"{table}: {was[table]} rows before, {back[table]} after the way back")
        verdict = "FAIL " + "; ".join(problems) if problems else "PASS"
        print(f"revisions {start_rev} -> {head} -> {args.before}; series {series}; sources {sources} {types}; "
              f"rows before {was}; after upgrade {up_counts}; back {back}")
        print(f"ИЗМЕРЕНИЕ {args.id}: {verdict}")
        return 1 if problems else 0
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        print(f"ИЗМЕРЕНИЕ {args.id}: FAIL прибор: {type(exc).__name__}: {exc}")
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
