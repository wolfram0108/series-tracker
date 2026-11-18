"""One-time transfer of the sources of protocol 1 (`app://<door>/<card>`) to the titles of protocol 2 (`title://<site>/<id>`),
contracts/provider_socket.md, appendix B. DELETED in the commit of the deployment, with its test (tests/test_adopt_titles.py).

What it does, in order:
  1. asks the provider (`POST /v1/adopt`) which title each old address comes to;
  2. reads each such title (`POST /v1/read`, all rows) and matches every old row to a new row by (player, video): the token of a row is
     the same through every door, so the row keeps its identity and only its key (`<door>:<line>:<place>` -> `<line>:<place>`) and its
     link change;
  3. rewrites, in ONE transaction, everything the tracker keeps of the old addresses and keys: `series_sources.url` (the ids of the
     sources stay), `release_tables`, `release_rows`, `composition_sources`, `composition_rows`, `plan_assets` (the rows the library's
     files stand on), `plan_failures`. Two old sources of one series that come to one title become one (the first stays); the files
     that stood on the other are re-pointed to it.
A row of the old table the new one has no video for is LOST: the files that stand on it would lose their place in the plan, so the
transfer refuses to apply while any file stands on a lost row (`--allow-orphans` says the owner took that).

  adopt_titles.py --provider URL [--token T] [--db app.db]            the plan: what would change (nothing is written)
  adopt_titles.py --provider URL [--token T] [--db app.db] --apply    copies the database to `backups/`, then does it
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
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


@dataclass
class Move:
    """The way the rows of one old address go to the rows of its title: old key -> (new key, new link), and the keys that find no row."""

    old_url: str
    new_url: str
    keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    lost: list[str] = field(default_factory=list)


@dataclass
class Report:
    sources_moved: int = 0
    sources_merged: int = 0
    tables: int = 0
    rows_moved: int = 0
    rows_lost: int = 0
    assets_rewritten: int = 0
    orphaned: list[str] = field(default_factory=list)  # `<source_id> <old key>` of files that stand on a lost row
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


def build_moves(rows: dict[str, list[dict]], adopted: dict[str, str], tables: dict[str, list[dict]]) -> list[Move]:
    """Match the old rows of every adopted address to the rows of its title by (player, video)."""
    moves = []
    for old_url, old in rows.items():
        new_url = adopted.get(old_url)
        if new_url is None:
            continue
        fresh = {(each["player"], video_of(each)): (each["key"], each["link"]) for each in tables.get(new_url, [])}
        move = Move(old_url, new_url)
        for row in old:
            hit = fresh.get((row["player"], row["video"]))
            if hit is None:
                move.lost.append(row["key"])
            else:
                move.keys[row["key"]] = hit
        moves.append(move)
    return moves


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


def apply(conn: sqlite3.Connection, adopted: dict[str, str], moves: list[Move], *, site: str = "chines", allow_orphans: bool = False) -> Report:
    """The whole transfer in one transaction; `Refused` (and nothing written) when a file would lose its place."""
    report = Report()
    by_url = {each.old_url: each for each in moves}
    conn.execute("BEGIN IMMEDIATE")
    try:
        sources = conn.execute("SELECT id, series_id, position, url FROM series_sources WHERE url LIKE ? ORDER BY series_id, position, id", (OLD,)).fetchall()
        keeper: dict[tuple[int, str], int] = {}
        redirect: dict[int, int] = {}  # a source that is merged into another -> the one that stays
        for sid, series_id, _position, url in sources:
            if url not in adopted:
                report.unadopted.append(url)
                continue
            first = keeper.setdefault((series_id, adopted[url]), sid)
            if first != sid:
                redirect[sid] = first
        mapping: dict[tuple[int, str], tuple[int, str, str]] = {}
        lost: set[tuple[int, str]] = set()
        urls = {sid: url for sid, _series, _pos, url in sources}
        for sid, url in urls.items():
            move = by_url.get(url)
            if move is None:
                continue
            target = redirect.get(sid, sid)
            for old_key, (new_key, new_link) in move.keys.items():
                mapping[(sid, old_key)] = (target, new_key, new_link)
            lost.update((sid, key) for key in move.lost)

        # the files that stand on a row that is lost: refuse before anything is written
        report.rows_lost = sum(len(each.lost) for each in moves)
        orphans = []
        for series_id, name, data in conn.execute("SELECT series_id, name, data FROM plan_assets"):
            seen: list[tuple[int, str]] = []
            _walk(json.loads(data), mapping, lost, seen)
            orphans += [f"{series_id} {name}: {sid} {key}" for sid, key in dict.fromkeys(seen)]
        report.orphaned = orphans
        if orphans and not allow_orphans:
            raise Refused(f"{len(orphans)} files of the plan stand on rows the title has no video for: {orphans[:3]}")

        for sid, target in redirect.items():
            conn.execute("DELETE FROM composition_rows WHERE source_id = ?", (sid,))
            conn.execute("DELETE FROM composition_sources WHERE source_id = ?", (sid,))
            conn.execute("DELETE FROM series_sources WHERE id = ?", (sid,))
            report.sources_merged += 1
        for sid, series_id, _position, url in sources:
            if sid not in redirect and url in adopted:
                conn.execute("UPDATE series_sources SET url = ? WHERE id = ?", (adopted[url], sid))
                report.sources_moved += 1

        columns = [row[1] for row in conn.execute("PRAGMA table_info(release_rows)")]
        table_columns = [row[1] for row in conn.execute("PRAGMA table_info(release_tables)")]
        quoted = ", ".join(f'"{name}"' for name in columns)
        for old_url, new_url in adopted.items():
            move = by_url.get(old_url)
            if conn.execute("SELECT 1 FROM release_tables WHERE url = ?", (old_url,)).fetchone() is None:
                continue
            if conn.execute("SELECT 1 FROM release_tables WHERE url = ?", (new_url,)).fetchone() is None:
                picked = ", ".join("?" if name in ("url", "site") else f'"{name}"' for name in table_columns)
                params = [new_url if name == "url" else site for name in table_columns if name in ("url", "site")]
                conn.execute(f'INSERT INTO release_tables ({", ".join(f"{chr(34)}{name}{chr(34)}" for name in table_columns)}) '
                             f'SELECT {picked} FROM release_tables WHERE url = ?', (*params, old_url))
                report.tables += 1
            for row in conn.execute("SELECT * FROM release_rows WHERE url = ?", (old_url,)).fetchall():
                record = dict(zip(columns, row))
                hit = move.keys.get(record["key"]) if move else None
                if hit is None:
                    continue
                if conn.execute('SELECT 1 FROM release_rows WHERE url = ? AND "key" = ?', (new_url, hit[0])).fetchone():
                    continue  # the title already holds this row (another old address came to the same title first)
                taken = conn.execute("SELECT 1 FROM release_rows WHERE url = ? AND seq = ?", (new_url, record["seq"])).fetchone()
                if taken:
                    record["seq"] = conn.execute("SELECT MAX(seq) + 1 FROM release_rows WHERE url = ?", (new_url,)).fetchone()[0]
                record.update(url=new_url, key=hit[0], link=hit[1])
                conn.execute(f'INSERT INTO release_rows ({quoted}) VALUES ({", ".join("?" * len(columns))})', [record[name] for name in columns])
                report.rows_moved += 1
            conn.execute("DELETE FROM release_tables WHERE url = ?", (old_url,))  # its old rows go with it

        for sid, series_id, url, summary in conn.execute("SELECT source_id, series_id, table_url, summary FROM composition_sources WHERE table_url LIKE ?", (OLD,)).fetchall():
            new = adopted.get(url)
            if new is not None:
                conn.execute("UPDATE composition_sources SET table_url = ?, summary = ? WHERE source_id = ?", (new, summary.replace(url, new), sid))
        for (sid, old_key), (target, new_key, new_link) in mapping.items():
            if conn.execute("SELECT 1 FROM composition_rows WHERE source_id = ? AND row_key = ?", (target, new_key)).fetchone() and sid != target:
                conn.execute("DELETE FROM composition_rows WHERE source_id = ? AND row_key = ?", (sid, old_key))
                continue
            conn.execute("UPDATE composition_rows SET row_key = ?, link = ?, source_id = ? WHERE source_id = ? AND row_key = ?",
                         (new_key, new_link, target, sid, old_key))
        for sid, old_key in lost:
            conn.execute("DELETE FROM composition_rows WHERE source_id = ? AND row_key = ?", (sid, old_key))

        for series_id, name, data in conn.execute("SELECT series_id, name, data FROM plan_assets").fetchall():
            changed = _walk(json.loads(data), mapping, lost, [])
            text = json.dumps(changed, ensure_ascii=False)
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


async def ask_provider(url: str, token: str, olds: list[str]) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """What the provider says: the title of each old address, and the rows of each title (all of them, as the tracker reads)."""
    client = ProviderClient(url, token)
    try:
        reply = await client.adopt(AdoptRequest(urls=olds))
        adopted = {each.url: each.to for each in reply.items if each.to}
        tables: dict[str, list[dict]] = {}
        for new_url in dict.fromkeys(adopted.values()):
            async for frame in client.read(ReadRequest(url=new_url, rows="all")):
                if frame.type == FRAME_ERROR:
                    raise SystemExit(f"{new_url}: the provider could not read it: {frame.reason}")
                if frame.type == FRAME_DRAFT:
                    tables[new_url] = (frame.draft or {}).get("rows", [])
                    break
        return adopted, tables
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
    adopted, tables = asyncio.run(ask_provider(args.provider, args.token, olds))
    moves = build_moves(old_rows(conn), adopted, tables)
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
        report = apply(target, adopted, moves, allow_orphans=args.allow_orphans)
    except Refused as exc:
        raise SystemExit(f"refused: {exc}") from exc
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=1))
    print("APPLIED" if args.apply else "PLAN (nothing written)")


if __name__ == "__main__":
    main()
