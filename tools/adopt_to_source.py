"""One-time transfer of the files of ONE series to the sources the user added for it (the lines of a title of the provider).

The files of the library remember their video by the row it was made from: `(source, key of the row, video)`. The video is the
token of the row in its archive, the same through every door, so the identity of a row is (line, token), and a file does not lose
its place when the row it stands on is given by another source. This tool gives the files of a series to the new sources: every
reference of its plan (`plan_assets`, `plan_failures`) to a row of a source of protocol 1 (`app://…`), or of a source that was deleted,
is pointed to the row of the new source that has the same line and the same token; and the variants the user chose in the series
(`output_settings`: the order and the equality of the references, the references allowed) are pointed to the new source of their line.
Nothing else is touched: the files on disk, the references to the sources that stay (a voice-over of another site), the rows and the
variants of a line no new source is given for (the plan keeps a file whose row a source forgot).

The derived tables of the composition of the series are dropped: the service counts them again at its start (restart it).
DELETED in the commit of the deployment, with its test (tests/test_adopt_to_source.py).

  adopt_to_source.py --series 40 --source 192 --source 193 [--db app.db]            the report: what would change (nothing is written)
  adopt_to_source.py --series 40 --source 192 --source 193 [--db app.db] --apply    copies the database to `backups/`, then does it
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_PREFIX = "app://"
Hit = tuple[int, str, str, str]  # (source id, key, link, site) of a row of a new source


@dataclass
class Report:
    files: int = 0
    files_rewritten: int = 0
    refs_rewritten: int = 0
    failures_rewritten: int = 0
    settings_rewritten: int = 0
    settings_merged: int = 0
    unmatched: dict[tuple[int, str], set[str]] = field(default_factory=dict)  # (source id, player) -> the rows no new source lists
    untouched_sources: set[int] = field(default_factory=set)


class Refused(RuntimeError):
    """The transfer would be made on a wrong ground: nothing was written."""


def player_of(row_key: str) -> str | None:
    """The player of a row of protocol 1 by its key `<door>:<line>:<place>`; None for a key of another shape (a site's own)."""
    parts = row_key.split(":")
    return f"line:{parts[1]}" if len(parts) >= 3 and parts[1] else None


def _is_ref(node: Any) -> bool:
    return isinstance(node, dict) and isinstance(node.get("source_id"), int) and isinstance(node.get("row_key"), str)


def _point(snapshot: dict, hit: Hit) -> None:
    snapshot["link"], snapshot["source"] = hit[2], hit[3]


def rewrite(node: Any, lookup: Callable[[dict], Hit | None], counts: list[int]) -> Any:
    """The JSON of a file or a failure with every reference the lookup knows pointed to the new row, and the snapshots that stand beside
    a reference (where the file came from) told the same; `counts[0]` counts the references pointed."""
    if isinstance(node, dict):
        beside = {name: lookup(node[name]) for name in ("id", "dub", "timeline") if _is_ref(node.get(name))}
        if beside.get("id") and isinstance(node.get("snapshot"), dict):
            _point(node["snapshot"], beside["id"])
        elif beside.get("dub") and isinstance(node.get("snapshot"), dict) and "id" not in node:
            _point(node["snapshot"], beside["dub"])
        if beside.get("timeline") and isinstance(node.get("timeline_snapshot"), dict):
            _point(node["timeline_snapshot"], beside["timeline"])
        if _is_ref(node):
            hit = lookup(node)
            if hit:
                node["source_id"], node["row_key"] = hit[0], hit[1]
                counts[0] += 1
        return {key: rewrite(value, lookup, counts) for key, value in node.items()}
    if isinstance(node, list):
        if len(node) == 3 and isinstance(node[0], int) and isinstance(node[1], str) and isinstance(node[2], str):
            hit = lookup({"source_id": node[0], "row_key": node[1], "video": node[2]})
            if hit:
                counts[0] += 1
                return [hit[0], hit[1], node[2]]
        return [rewrite(each, lookup, counts) for each in node]
    return node


def _variants(node: Any, moved: Callable[[int, str], int | None]) -> Any:
    """A JSON value of a setting with every variant `{source_id, player, ...}` of a moved line pointed to its new source; the same variant
    twice after that is once (the order of the list keeps the first)."""
    if isinstance(node, dict):
        if isinstance(node.get("source_id"), int) and isinstance(node.get("player"), str):
            new = moved(node["source_id"], node["player"])
            if new is not None:
                node = {**node, "source_id": new}
        return {key: _variants(value, moved) for key, value in node.items()}
    if isinstance(node, list):
        out: list = []
        for each in (_variants(item, moved) for item in node):
            if each not in out:
                out.append(each)
        return out
    return node


def apply(conn: sqlite3.Connection, series_id: int, source_ids: list[int]) -> Report:
    """The whole transfer in one transaction; `Refused` (and nothing written) when it would rest on a wrong ground."""
    report = Report()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows: dict[tuple[str, str], Hit] = {}
        by_player: dict[str, int] = {}
        for source_id in source_ids:
            found = conn.execute("SELECT url FROM series_sources WHERE id = ? AND series_id = ?", (source_id, series_id)).fetchone()
            if found is None:
                raise Refused(f"the source {source_id} is no source of the series {series_id}")
            url = found[0]
            table = conn.execute("SELECT site, state FROM release_tables WHERE url = ?", (url,)).fetchone()
            if table is None or table[1] != "ready":
                raise Refused(f"the source {source_id} ({url}) has no table that is ready: read it first")
            held = {(player, video): (source_id, key, link, table[0]) for key, player, video, link in
                    conn.execute('SELECT "key", player, video, link FROM release_rows WHERE url = ?', (url,))}
            if not held:
                raise Refused(f"the table of {url} holds no row")
            rows.update(held)
            by_player[f"line:{url.rstrip('/').rsplit('/', 1)[-1]}"] = source_id
        if conn.execute("SELECT 1 FROM plan_runs WHERE series_id = ?", (series_id,)).fetchone():
            raise Refused(f"a sync of the series {series_id} is under way: do it when it ends")
        existing = {sid: u for sid, u in conn.execute("SELECT id, url FROM series_sources")}

        def old(sid: int) -> bool:
            """A source that was of protocol 1 or is deleted: what stands on it is given away; a source that stays keeps its own."""
            return sid not in source_ids and (sid not in existing or existing[sid].startswith(OLD_PREFIX))

        def lookup(ref: dict) -> Hit | None:
            sid = ref["source_id"]
            if sid in source_ids:
                return None
            if not old(sid):
                report.untouched_sources.add(sid)
                return None
            player = player_of(ref["row_key"])
            hit = rows.get((player, ref.get("video") or "")) if player else None
            if hit is None:
                report.unmatched.setdefault((sid, player or "?"), set()).add(ref["row_key"])
            return hit

        def moved(sid: int, player: str) -> int | None:
            return by_player.get(player) if old(sid) else None

        for name, data in conn.execute("SELECT name, data FROM plan_assets WHERE series_id = ?", (series_id,)).fetchall():
            report.files += 1
            counts = [0]
            text = json.dumps(rewrite(json.loads(data), lookup, counts), ensure_ascii=False)
            if counts[0]:
                conn.execute("UPDATE plan_assets SET data = ? WHERE series_id = ? AND name = ?", (text, series_id, name))
                report.files_rewritten += 1
                report.refs_rewritten += counts[0]
        for (key,) in conn.execute("SELECT key FROM plan_failures WHERE series_id = ?", (series_id,)).fetchall():
            counts = [0]
            changed = json.dumps(rewrite(json.loads(key), lookup, counts), ensure_ascii=False, separators=(",", ":"))
            if counts[0]:
                conn.execute("DELETE FROM plan_failures WHERE series_id = ? AND key = ?", (series_id, key))
                conn.execute("INSERT OR IGNORE INTO plan_failures (series_id, key) VALUES (?, ?)", (series_id, changed))
                report.failures_rewritten += 1

        for rowid, lever, target, value, season, episode, compilation in conn.execute(
                "SELECT rowid, lever, target, value, season, episode, compilation FROM output_settings WHERE series_id = ? ORDER BY rowid",
                (series_id,)).fetchall():
            new_target, new_value = target, value
            if target and target.startswith("variant:"):
                label, sid, player = json.loads(target[len("variant:"):])
                new = moved(sid, player)
                if new is not None:
                    new_target = "variant:" + json.dumps([label, new, player], ensure_ascii=False, separators=(",", ":"))
            if lever in ("reference.order", "reference.same"):
                new_value = json.dumps(_variants(json.loads(value), moved), ensure_ascii=False)
            if (new_target, new_value) == (target, value):
                continue
            slot = conn.execute("SELECT 1 FROM output_settings WHERE series_id = ? AND IFNULL(season, -1) = IFNULL(?, -1) AND IFNULL(episode, -1) = "
                                "IFNULL(?, -1) AND IFNULL(compilation, '') = IFNULL(?, '') AND lever = ? AND IFNULL(target, '') = IFNULL(?, '') "
                                "AND rowid != ?", (series_id, season, episode, compilation, lever, new_target, rowid)).fetchone()
            if slot is not None:  # two old variants became one: the first word of the user stands, the second goes
                conn.execute("DELETE FROM output_settings WHERE rowid = ?", (rowid,))
                report.settings_merged += 1
            else:
                conn.execute("UPDATE output_settings SET target = ?, value = ? WHERE rowid = ?", (new_target, new_value, rowid))
                report.settings_rewritten += 1

        conn.execute("DELETE FROM composition_rows WHERE series_id = ?", (series_id,))  # derived: the service counts them again
        conn.execute("DELETE FROM composition_sources WHERE series_id = ?", (series_id,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=os.path.join(ROOT, "app.db"))
    parser.add_argument("--series", type=int, required=True)
    parser.add_argument("--source", type=int, action="append", required=True, help="the id of a source the user added (one for each line)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    target = conn
    if args.apply:
        os.makedirs(os.path.join(ROOT, "backups"), exist_ok=True)
        backup = os.path.join(ROOT, "backups", f"app.db.before-adopt-series-{args.series}-{time.strftime('%Y%m%d-%H%M%S')}")
        with sqlite3.connect(backup) as copy:
            conn.backup(copy)
        print(f"backup: {backup}")
    else:  # the report is made on a copy: nothing of the database is written
        target = sqlite3.connect(":memory:", isolation_level=None)
        target.execute("PRAGMA foreign_keys = ON")
        conn.backup(target)
    try:
        report = apply(target, args.series, args.source)
    except Refused as exc:
        raise SystemExit(f"refused: {exc}") from exc
    print(json.dumps({"files": report.files, "files_rewritten": report.files_rewritten, "refs_rewritten": report.refs_rewritten,
                      "failures_rewritten": report.failures_rewritten, "settings_rewritten": report.settings_rewritten,
                      "settings_merged": report.settings_merged,
                      "unmatched (old source, player): rows": {f"{sid} {player}": len(each) for (sid, player), each in sorted(report.unmatched.items())},
                      "sources that stay": sorted(report.untouched_sources)}, ensure_ascii=False, indent=1))
    print("APPLIED: restart the service" if args.apply else "REPORT (nothing written)")


if __name__ == "__main__":
    main()
