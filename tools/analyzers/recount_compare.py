"""The best, the median and the spread of the rounds of runs of `tools/checks/recount_profile.py`, the tree before a change against
the tree after it.

    recount_compare.py <folder with old_<N>.jsonl and new_<N>.jsonl>

Each file is one run of `recount_profile.py --out` (`old_*` measured with `--root` on the commit before the change, `new_*` on the
tree after it). The raw files are only read. For the phase `plan_view` rounds 1-2 are cold (the names are asked of the rules one
request a target), so the warm rounds 3-5 are read apart, as the measurements of stage 0 of `modular-architecture` did. A run on a
loaded box varies by a third: compare the best of each run and the median, not one number. The last line is `COMPARE_DONE`.
"""
import glob
import json
import statistics
import sys

folder = sys.argv[1]
table: dict = {}
for tree in ("old", "new"):
    for path in sorted(glob.glob(f"{folder}/{tree}_*.jsonl")):
        run = path.rsplit("_", 1)[1].split(".")[0]
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            table.setdefault((row["series"], row["phase"], tree), {}).setdefault(run, []).append((row["round"], row["wall_ms"]))
for series, phase in sorted({(key[0], key[1]) for key in table}):
    for scope, rounds in (("all rounds", (1, 2, 3, 4, 5)), ("warm 3-5", (3, 4, 5))):
        if phase == "layout" and scope == "warm 3-5":
            continue
        print(f"series {series} {phase} [{scope}] ms:")
        for tree in ("old", "new"):
            runs = table.get((series, phase, tree), {})
            best_of_run = [min(v for r, v in vals if r in rounds) for _, vals in sorted(runs.items())]
            every = [v for vals in runs.values() for r, v in vals if r in rounds]
            print(f"  {tree}: runs {len(runs)}  best of each run {[round(x) for x in best_of_run]}  "
                  f"best {round(min(every))}  median {round(statistics.median(every))}  max {round(max(every))}")
print("COMPARE_DONE")
