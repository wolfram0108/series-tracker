"""Random histories of changes and faults through the planner of the table of an episode (core/plan), in the world of the
simulator (tests/plan_world.py, Chaos): after every sync the invariants of the model are checked, and when the faults stop
the series must come to rest.

Two worlds: a small one of two seasons (`--toy N`: N histories), and the world of a real series (`--series ID...`): read by
plan_real (a copy of the database, the real constructor), its waiting work done first, then histories of changes and
faults on its own rows, files, sources, seasons, episodes and groups.

Usage: plan_fuzz.py [--toy N] [--series ID ...] [--histories H] [--steps K] [--seed S]
Prints a line per kind of violation with its first seeds, then `PLAN_FUZZ_DONE`."""
from __future__ import annotations

import argparse
import copy
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, HERE, os.path.join(ROOT, "tests")]

from plan_world import Chaos, World  # noqa: E402

# On a real series: changes of what is there, no invented rows of invented sources.
REAL_ACTIONS = ("drop", "renumber", "move_season", "mass_shift", "wrong_number", "higher", "swap_video", "delete_video",
                "delete_track", "off", "on", "off_group", "off_compilation", "reference_order", "shuffle_voices",
                "read_fail", "download_fail", "transient", "answer", "cut_answer", "lose_file", "reset")


def run(histories, make, steps: int, first_seed: int) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for seed in range(first_seed, first_seed + histories):
        try:
            make(seed).history(steps)
        except AssertionError as exc:
            found.setdefault(str(exc).split(":")[0][:90], []).append(seed)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toy", type=int, default=0)
    parser.add_argument("--series", type=int, nargs="*", default=[])
    parser.add_argument("--histories", type=int, default=5)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="/tmp")
    args = parser.parse_args()
    if args.toy:
        began = time.monotonic()
        found = run(args.toy, lambda seed: Chaos(World(), random.Random(seed)), args.steps, args.seed)
        print(f"TOY histories={args.toy} steps={args.steps} seconds={time.monotonic() - began:.0f} violations={len(found)}")
        for kind, seeds in found.items():
            print(f"  VIOLATION {len(seeds)} {kind} seeds={seeds[:6]}")
    if args.series:
        import plan_real
        from plan_situations import world_of
        real = plan_real.RealSeries(plan_real.copy_db(os.path.join(args.out, "app-copy.db")))
        for series_id in args.series:
            began = time.monotonic()
            inputs, _, info = plan_real.run(plan_real.inputs_of(real, series_id))
            base = world_of(inputs)
            base.sync("работа, которая ждёт сейчас")
            slots = sorted({row.slot for row in inputs.online if not row.compilation})
            seasons = tuple(sorted({season for season, _ in slots})) or (1,)
            episodes = tuple(sorted({episode for _, episode in slots})) or (1,)
            groups = tuple(sorted({row.group for row in inputs.online if row.group})) or ("A",)
            reference = max(groups, key=lambda group: sum(row.group == group for row in inputs.online))

            def make(seed, base=base, seasons=seasons, episodes=episodes, groups=groups, reference=reference):
                return Chaos(copy.deepcopy(base), random.Random(seed), seasons, episodes, reference, groups, REAL_ACTIONS)
            found = run(args.histories, make, args.steps, args.seed)
            print(f"SERIES {series_id} histories={args.histories} steps={args.steps} "
                  f"seconds={time.monotonic() - began:.0f} violations={len(found)}")
            for kind, seeds in found.items():
                print(f"  VIOLATION {len(seeds)} {kind} seeds={seeds[:6]}")
        os.remove(real.path)
    print("PLAN_FUZZ_DONE")


if __name__ == "__main__":
    main()
