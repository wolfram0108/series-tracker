"""One-time transfer of the sources of protocol 1 (`app://<door>/<card>`, a card with many lines) to the sources of protocol 2 (a LINE
of a title, `title://<site>/<id>/<line>`), contracts/provider_socket.md, appendix B. DELETED in the commit of the deployment, with its
test (tests/test_adopt_titles.py).

What it does, in order:
  1. asks the provider (`POST /v1/adopt`) which source of protocol 2 each line of each old card comes to;
  2. reads each such line (`POST /v1/read`, all its rows) and matches every old row to a new row by (player, video): the token of a row
     is the same through every door, so the row keeps its identity and only its key (`<door>:<line>:<place>` -> `<line>:<place>`) and
     its link change;
  3. rewrites, in ONE transaction, everything the tracker keeps of the old addresses and keys: `series_sources` (a card with several
     lines becomes several sources: the one with most rows keeps the number of the old source, the others are new with the same
     settings), `release_tables`, `release_rows`, `plan_assets` (the rows the library's files stand on), `plan_failures`. The derived
     tables of the composition of the series are dropped: the service counts them again at its start. Two old sources of one series
     that come to one line become one (the first stays); the files that stood on the other are re-pointed to it.
A row of the old table the provider does not play (a line it has no target for, or a row its line has no video for) is LOST: the files
that stand on it would lose their place in the plan, so the transfer refuses to apply while any file stands on a lost row
(`--allow-orphans` says the owner took that).

  adopt_titles.py --provider URL [--token T] [--db app.db]            the plan: what would change (nothing is written)
  adopt_titles.py --provider URL [--token T] [--db app.db] --apply    copies the database to `backups/`, then does it
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.contracts.provider import FRAME_DRAFT, FRAME_ERROR, AdoptRequest, ReadRequest  # noqa: E402
from modules.sources.provider.client import ProviderClient  # noqa: E402

OLD = "app://%"
NEW = "title://%"
# old address -> [(code of a line, address of the source of that line)], as the provider adopts it
Targets = dict[str, list[tuple[str, str]]]


@dataclass
class LineMove:
    """The rows of one line of one old address and where they go: old key -> (new key, new link), and the keys that find no row."""

    old_url: str
    line: str
    new_url: str
    keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    lost: list[str] = field(default_factory=list)


@dataclass
class Plan:
    moves: list[LineMove] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)  # old address -> keys of the rows of lines the provider has no target for


@dataclass
class Report:
    sources_moved: int = 0
    sources_created: int = 0
    sources_merged: int = 0
    tables: int = 0
    rows_moved: int = 0
    rows_lost: int = 0
    assets_rewritten: int = 0
    orphaned: list[str] = field(default_factory=list)  # `<series> <file>: <source_id> <old key>` of files that stand on a lost row
    failures_rewritten: int = 0
    unadopted: list[str] = field(default_factory=list)


class Refused(RuntimeError):
    """The transfer would lose a place of a file: nothing was written."""


def old_rows(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for url, key, player, video in conn.execute("SELECT url, key, player, video FROM release_rows WHERE url LIKE ?", (OLD,)):
        found.setdefault(url, []).append({"key": key, "player": player, "video": video})
    return found


def video_of(row: dict) -> str:
    """The video of a row of the new table: the field when the provider filled it, else the token its link names (`line://<line>/<token>`)."""
    return row.get("video") or unquote(row.get("link", "").rpartition("/")[2])


def build_plan(rows: dict[str, list[dict]], targets: Targets, tables: dict[str, dict]) -> Plan:
    """Match the old rows of every line of every adopted address to the rows of its line source by (player, video)."""
    plan = Plan()
    for old_url, old in rows.items():
        wanted = dict(targets.get(old_url) or [])
        if not wanted:
            continue
        for line, new_url in wanted.items():
            fresh = {(each["player"], video_of(each)): (each["key"], each["link"]) for each in tables.get(new_url, {}).get("rows", [])}
            move = LineMove(old_url, line, new_url)
            for row in old:
                if row["player"] != f"line:{line}":
                    continue
                hit = fresh.get((row["player"], row["video"]))
                if hit is None:
                    move.lost.append(row["key"])
                else:
                    move.keys[row["key"]] = hit
            plan.moves.append(move)
        plan.dropped[old_url] = [row["key"] for row in old if row["player"].removeprefix("line:") not in wanted]
    return plan


def _walk(node: Any, mapping: dict[tuple[int, str], tuple[int, str, str]], lost: set[tuple[int, str]], seen: list[tuple[int, str]]) -> Any:
    """Rewrite a JSON value: every `{source_id, row_key, ...}` and every `[source_id, row_key, video]` that names a moved row gets
    the new source and key; the `snapshot` beside an `id` gets the new link. Rows named that are lost are noted in `seen`."""
    if isinstance(node, dict):
        if isinstance(node.get("source_id"), int) and isinstance(node.get("row_key"), str):
            ref = (node["source_id"], node["row_key"])
            if ref in mapping:
                node["source_id"], node["row_key"] = mapping[ref][0], mapping[ref][1]
            elif ref in lost:
                seen.append(ref)
        ident = node.get("id")
        if isinstance(ident, dict) and isinstance(node.get("snapshot"), dict):
            ref = (ident.get("source_id"), ident.get("row_key"))
            if ref in mapping:
                node["snapshot"]["link"] = mapping[ref][2]  # before the id itself is rewritten: the old reference names the row
        return {key: _walk(value, mapping, lost, seen) for key, value in node.items()}
    if isinstance(node, list):
        if len(node) == 3 and isinstance(node[0], int) and isinstance(node[1], str) and isinstance(node[2], str):
            ref = (node[0], node[1])
            if ref in mapping:
                return [mapping[ref][0], mapping[ref][1], node[2]]
            if ref in lost:
                seen.append(ref)
        return [_walk(each, mapping, lost, seen) for each in node]
    return node


def apply(conn: sqlite3.Connection, targets: Targets, plan: Plan, tables: dict[str, dict], *, site: str = "chines",
          allow_orphans: bool = False) -> Report:
    """The whole transfer in one transaction; `Refused` (and nothing written) when a file would lose its place."""
    report = Report()
    by_old: dict[str, dict[str, LineMove]] = {}
    for move in plan.moves:
        by_old.setdefault(move.old_url, {})[move.line] = move
    report.rows_lost = sum(len(each.lost) for each in plan.moves) + sum(len(each) for each in plan.dropped.values())
    conn.execute("BEGIN IMMEDIATE")
    try:
        sources = conn.execute("SELECT id, series_id, url, source_type, parser_profile_id, vibix_season, players_off, color, rows_per_line "
                               "FROM series_sources WHERE url LIKE ? ORDER BY series_id, position, id", (OLD,)).fetchall()
        mapping: dict[tuple[int, str], tuple[int, str, str]] = {}
        lost: set[tuple[int, str]] = set()
        # (series, address of a line) -> the source that holds it: a line the series already has is never a second source
        owner: dict[tuple[int, str], int] = {(series_id, url): sid for sid, series_id, url in
                                             conn.execute("SELECT id, series_id, url FROM series_sources WHERE url LIKE ?", (NEW,))}
        series_touched: set[int] = set()
        next_position: dict[int, int] = {}
        for sid, series_id, url, source_type, profile, vibix, off, color, rows_per_line in sources:
            lines = by_old.get(url, {})
            if not lines:
                report.unadopted.append(url)
                continue
            series_touched.add(series_id)
            if series_id not in next_position:
                next_position[series_id] = (conn.execute("SELECT MAX(position) FROM series_sources WHERE series_id = ?", (series_id,)).fetchone()[0] or 0) + 1
            # the line with most rows keeps the number of the source, ties by code: the same choice on every run
            order = sorted(lines, key=lambda each: (-len(lines[each].keys), each))
            kept = False
            for line in order:
                move = lines[line]
                key = (series_id, move.new_url)
                if key in owner:  # the series already has this line (another old source came to it first): this one is merged into it
                    target = owner[key]
                elif not kept:
                    kept, target = True, sid
                    conn.execute("UPDATE series_sources SET url = ? WHERE id = ?", (move.new_url, sid))
                    report.sources_moved += 1
                else:
                    target = conn.execute("INSERT INTO series_sources (series_id, position, url, source_type, parser_profile_id, vibix_season, "
                                          "players_off, color, rows_per_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                          (series_id, next_position[series_id], move.new_url, source_type, profile, vibix, off, color,
                                           rows_per_line)).lastrowid
                    next_position[series_id] += 1
                    report.sources_created += 1
                owner[key] = target
                for old_key, (new_key, new_link) in move.keys.items():
                    mapping[(sid, old_key)] = (target, new_key, new_link)
                lost.update((sid, old_key) for old_key in move.lost)
            lost.update((sid, old_key) for old_key in plan.dropped.get(url, []))
            if not kept:  # every line of it is already a source of the series
                conn.execute("DELETE FROM series_sources WHERE id = ?", (sid,))
                report.sources_merged += 1

        # the files that stand on a row that is lost: refuse before anything else is written
        orphans = []
        for series_id, name, data in conn.execute("SELECT series_id, name, data FROM plan_assets").fetchall():
            seen: list[tuple[int, str]] = []
            _walk(json.loads(data), mapping, lost, seen)
            orphans += [f"{series_id} {name}: {sid} {key}" for sid, key in dict.fromkeys(seen)]
        report.orphaned = orphans
        if orphans and not allow_orphans:
            raise Refused(f"{len(orphans)} files of the plan stand on rows the provider has no video for: {orphans[:3]}")

        columns = [row[1] for row in conn.execute("PRAGMA table_info(release_rows)")]
        table_columns = [row[1] for row in conn.execute("PRAGMA table_info(release_tables)")]
        for old_url, lines in by_old.items():
            old = conn.execute("SELECT * FROM release_tables WHERE url = ?", (old_url,)).fetchone()
            if old is None:
                continue
            old_table = dict(zip(table_columns, old))
            for move in lines.values():
                if conn.execute("SELECT 1 FROM release_tables WHERE url = ?", (move.new_url,)).fetchone() is None:
                    draft = tables.get(move.new_url, {})
                    names = draft.get("names") or json.loads(old_table.get("names") or "[]")
                    made = {**old_table, "url": move.new_url, "site": site, "title": draft.get("title") or old_table.get("title", ""),
                            "names": json.dumps(names, ensure_ascii=False)}
                    conn.execute(f'INSERT INTO release_tables ({", ".join(f"{chr(34)}{name}{chr(34)}" for name in table_columns)}) '
                                 f'VALUES ({", ".join("?" * len(table_columns))})', [made[name] for name in table_columns])
                    report.tables += 1
                for row in conn.execute("SELECT * FROM release_rows WHERE url = ?", (old_url,)).fetchall():
                    record = dict(zip(columns, row))
                    hit = move.keys.get(record["key"])
                    if hit is None:
                        continue
                    if conn.execute('SELECT 1 FROM release_rows WHERE url = ? AND "key" = ?', (move.new_url, hit[0])).fetchone():
                        continue  # the table of the line already holds this row (another old address came to the same line first)
                    if conn.execute("SELECT 1 FROM release_rows WHERE url = ? AND seq = ?", (move.new_url, record["seq"])).fetchone():
                        record["seq"] = conn.execute("SELECT MAX(seq) + 1 FROM release_rows WHERE url = ?", (move.new_url,)).fetchone()[0]
                    record.update(url=move.new_url, key=hit[0], link=hit[1])
                    conn.execute(f'INSERT INTO release_rows ({", ".join(f"{chr(34)}{name}{chr(34)}" for name in columns)}) '
                                 f'VALUES ({", ".join("?" * len(columns))})', [record[name] for name in columns])
                    report.rows_moved += 1
            conn.execute("DELETE FROM release_tables WHERE url = ?", (old_url,))  # its old rows go with it

        for series_id in series_touched:  # derived tables: the service counts them again at its start
            conn.execute("DELETE FROM composition_rows WHERE series_id = ?", (series_id,))
            conn.execute("DELETE FROM composition_sources WHERE series_id = ?", (series_id,))
        for series_id, name, data in conn.execute("SELECT series_id, name, data FROM plan_assets").fetchall():
            text = json.dumps(_walk(json.loads(data), mapping, lost, []), ensure_ascii=False)
            if text != json.dumps(json.loads(data), ensure_ascii=False):
                conn.execute("UPDATE plan_assets SET data = ? WHERE series_id = ? AND name = ?", (text, series_id, name))
                report.assets_rewritten += 1
        for series_id, key in conn.execute("SELECT series_id, key FROM plan_failures").fetchall():
            changed = json.dumps(_walk(json.loads(key), mapping, lost, []), ensure_ascii=False, separators=(",", ":"))
            if changed != key:
                conn.execute("DELETE FROM plan_failures WHERE series_id = ? AND key = ?", (series_id, key))
                conn.execute("INSERT OR IGNORE INTO plan_failures (series_id, key) VALUES (?, ?)", (series_id, changed))
                report.failures_rewritten += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return report


async def ask_provider(url: str, token: str, olds: list[str]) -> tuple[Targets, dict[str, dict]]:
    """What the provider says: the line sources each old address comes to, and the table of each (all its rows, as the tracker reads)."""
    client = ProviderClient(url, token)
    try:
        reply = await client.adopt(AdoptRequest(urls=olds))
        targets: Targets = {each.url: [(target.line, target.to) for target in each.targets if target.to] for each in reply.items if each.targets}
        tables: dict[str, dict] = {}
        for new_url in dict.fromkeys(to for each in targets.values() for _line, to in each):
            async for frame in client.read(ReadRequest(url=new_url, rows="all")):
                if frame.type == FRAME_ERROR:
                    raise SystemExit(f"{new_url}: the provider could not read it: {frame.reason}")
                if frame.type == FRAME_DRAFT:
                    tables[new_url] = frame.draft or {}
                    break
        return targets, tables
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=os.path.join(ROOT, "app.db"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-orphans", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    olds = [row[0] for row in conn.execute("SELECT DISTINCT url FROM series_sources WHERE url LIKE ?", (OLD,))]
    targets, tables = asyncio.run(ask_provider(args.provider, args.token, olds))
    plan = build_plan(old_rows(conn), targets, tables)
    target = conn
    if args.apply:
        os.makedirs(os.path.join(ROOT, "backups"), exist_ok=True)
        backup = os.path.join(ROOT, "backups", f"app.db.before-adopt-{time.strftime('%Y%m%d-%H%M%S')}")
        with sqlite3.connect(backup) as copy:
            conn.backup(copy)
        print(f"backup: {backup}")
    else:  # the plan is made on a copy: nothing of the database is written
        target = sqlite3.connect(":memory:", isolation_level=None)
        target.execute("PRAGMA foreign_keys = ON")
        conn.backup(target)
    try:
        report = apply(target, targets, plan, tables, allow_orphans=args.allow_orphans)
    except Refused as exc:
        raise SystemExit(f"refused: {exc}") from exc
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=1))
    print("APPLIED" if args.apply else "PLAN (nothing written)")


if __name__ == "__main__":
    main()
