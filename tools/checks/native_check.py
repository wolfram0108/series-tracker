"""The native core against the module it replaces, on a copy of the base of the service (docs/missions/rust-core).

For every series of the copy: the data the core holds is the data the module makes, field by field, and the time of
each way of making it (best of three). The stage of the mission is the subcommand; `levers` — the levers of every episode and the headers of every level
(the core counts them from the module's records); `rows` — the rows of the layout:

  python  — the module's own read (modules/composition/repository: the rows as models);
  given   — the module reads the table with sqlite3 and hands the core the tuples of its query;
  core    — the core reads the module's table itself;

and the reads of every series in threads at once against one after another (the core lets the lock go while it reads).

Usage: .venv/bin/python tools/checks/native_check.py rows [series id ...]   (none: every series of the copy)
Output: a line per series, the totals, then the line NATIVE_CHECK_DONE. Nothing is written but the copy.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core import _native  # noqa: E402
from modules.composition import repository  # noqa: E402
from tools.checks.plan_real import copy_db  # noqa: E402

FIELDS = [name.strip() for name in _native.COLUMNS.split(",")]


def best(fn, *args, times: int = 3):
    found, out = None, None
    for _ in range(times):
        begun = time.perf_counter()
        out = fn(*args)
        spent = time.perf_counter() - begun
        found = spent if found is None else min(found, spent)
    return out, found * 1000


def model_fields(row) -> tuple:
    """A row of the module in the order of the columns of the core (`grp` is the field `group`)."""
    values = row.model_dump(mode="json")
    return tuple(values["group" if name == "grp" else name] for name in FIELDS)


def rows_stage(path: str, wanted: list[int]) -> None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    series = wanted or [row[0] for row in conn.execute("SELECT DISTINCT series_id FROM composition_rows ORDER BY 1")]
    plain = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    query = f"SELECT {_native.COLUMNS} FROM composition_rows WHERE series_id = ? ORDER BY seq"
    totals = {"python": 0.0, "given": 0.0, "core": 0.0}
    differ_all = 0
    for series_id in series:
        stored, python_ms = best(repository._read, conn, series_id)
        modelled = sorted((model_fields(row) for row in [*stored.rows, *stored.subtitles]), key=lambda each: each[:3])
        fetched, fetch_ms = best(lambda: plain.execute(query, (series_id,)).fetchall())
        given, hand_ms = best(_native.layout_rows_given, fetched)
        held, core_ms = best(_native.layout_rows, path, series_id)
        mine = sorted((tuple(each) for each in held.tuples()), key=lambda each: each[:3])
        theirs = sorted((tuple(each) for each in given.tuples()), key=lambda each: each[:3])
        differ = sum(1 for one, two in zip(modelled, mine) if one != two) + abs(len(modelled) - len(mine))
        differ += sum(1 for one, two in zip(mine, theirs) if one != two)
        differ_all += differ
        totals["python"] += python_ms
        totals["given"] += fetch_ms + hand_ms
        totals["core"] += core_ms
        print(f"series {series_id:4}: rows {len(held):6}  python {python_ms:7.1f} ms | given: query {fetch_ms:6.1f} + "
              f"hand over {hand_ms:6.1f} ms | core reads {core_ms:6.1f} ms | differ {differ}", flush=True)
    print(f"all {len(series)} series: python {totals['python']:.0f} ms, given {totals['given']:.0f} ms, "
          f"core {totals['core']:.0f} ms, rows that differ {differ_all}")
    work = series * 4
    begun = time.perf_counter()
    for series_id in work:
        _native.layout_rows(path, series_id)
    alone = time.perf_counter() - begun
    threads = min(8, os.cpu_count() or 1)
    with ThreadPoolExecutor(threads) as pool:
        begun = time.perf_counter()
        list(pool.map(lambda series_id: _native.layout_rows(path, series_id), work))
        together = time.perf_counter() - begun
    print(f"core reads of {len(work)} series: one after another {alone * 1000:.0f} ms, in {threads} threads "
          f"{together * 1000:.0f} ms")


def levers_stage(path: str, wanted: list[int]) -> None:
    """The levers of every episode and the headers of every level, counted by the core from the module's records against
    the module's own count (modules/composition/settings), as JSON and byte by byte."""
    import json
    from modules.composition import compilations, levers, project, settings  # noqa: F401 - levers registers in the core
    from modules.composition.module import _source_order
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    series = wanted or [row[0] for row in conn.execute("SELECT DISTINCT series_id FROM composition_state ORDER BY 1")]
    differ_all = 0
    totals = {"python": 0.0, "core": 0.0}
    for series_id in series:
        stored = repository._read(conn, series_id)

        def python():
            records = settings.canonical(stored.records)
            episodes = project.layout_episodes(stored.rows)
            outcome = compilations.resolve(stored.rows, records, _source_order(stored))
            views = [(view.season, view.ref.key) for view in outcome.views]
            return (settings.levels(records, {season for season, _ in episodes}, views),
                    settings.episode_settings(records, episodes, outcome.members), episodes, outcome, views)
        (levels, settings_of, episodes, outcome, views), python_ms = best(python)
        given = [(season, episode, member.key if member else None, json.dumps(
            member.model_dump(mode="json") if member else None, ensure_ascii=False, separators=(",", ":")))
            for (season, episode), member in ((each, outcome.members.get(each)) for each in episodes)]
        (core_levels, core_settings), core_ms = best(
            _native.layout_levers, path, series_id, sorted({season for season, _ in episodes}), views, given)
        theirs_levels = json.dumps([each.model_dump(mode="json") for each in levels], ensure_ascii=False, separators=(",", ":"))
        theirs_settings = json.dumps([each.model_dump(mode="json") for each in settings_of], ensure_ascii=False,
                                     separators=(",", ":"))
        differ = (json.loads(core_levels) != json.loads(theirs_levels)) + (json.loads(core_settings) != json.loads(theirs_settings))
        bytes_differ = (core_levels != theirs_levels) + (core_settings != theirs_settings)
        differ_all += differ + bytes_differ
        totals["python"] += python_ms
        totals["core"] += core_ms
        print(f"series {series_id:4}: episodes {len(episodes):4} records {len(stored.records):4}  python {python_ms:7.1f} ms | "
              f"core {core_ms:6.1f} ms | differ {differ}, bytes differ {bytes_differ}", flush=True)
    print(f"all {len(series)} series: python {totals['python']:.0f} ms, core {totals['core']:.0f} ms (the core reads its "
          f"records itself; python counts compilations too), differ {differ_all}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stage", choices=["rows", "levers"])
    parser.add_argument("series", nargs="*", type=int)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        path = copy_db(os.path.join(folder, "app.db"))
        (rows_stage if options.stage == "rows" else levers_stage)(path, options.series)
    print("NATIVE_CHECK_DONE")


if __name__ == "__main__":
    main()
