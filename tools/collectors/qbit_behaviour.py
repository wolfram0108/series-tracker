"""qBittorrent behaviour on a release update, recorded from the real client (stage 15, step 0).

Runs the update scenario of tests/qbit_model.py (the same scenario the model of the client is checked by) on the client
of `.env` (`ST_QBIT_*`), in the category `st-probe` and a probe folder; records every observation: client state,
per-file progress and priority, pieces, the files and folders on disk with sizes and mtimes. Nothing but the probe
torrents, the category and the folder is touched; all three are removed at the end.

Usage: .venv/bin/python tools/collectors/qbit_behaviour.py --host-root /home/user/qbit/downloads --client-root /downloads
Writes data/raw/qbit_behaviour__<when>__probe.jsonl and prints QBIT_BEHAVIOUR_DONE.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from modules.torrents.qbt_client import QbtClient  # noqa: E402
from qbit_model import UpdateScenario  # noqa: E402

CATEGORY = "st-probe"
PREFS = ("save_path", "temp_path_enabled", "incomplete_files_ext", "preallocate_all", "add_stopped_enabled",
         "auto_tmm_enabled", "torrent_content_layout", "use_unwanted_folder", "recheck_completed_torrents")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-root", required=True)
    ap.add_argument("--client-root", required=True)
    args = ap.parse_args()
    env = dict(line.strip().split("=", 1) for line in open(ROOT / ".env") if "=" in line and not line.startswith("#"))
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"st-probe-{when}"
    folder = Path(args.host_root) / name
    raw = ROOT / "data" / "raw" / f"qbit_behaviour__{when}__probe.jsonl"
    client = QbtClient(env["ST_QBIT_URL"], env["ST_QBIT_USER"], env["ST_QBIT_PASS"])
    await client.login()
    out = raw.open("w", encoding="utf-8")

    def record(scenario: str, step: str, **fields) -> None:
        row = {"scenario": scenario, "step": step, "at": datetime.now(timezone.utc).isoformat(), **fields}
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()

    scenario = UpdateScenario(client, folder, f"{args.client_root}/{name}", record, category=CATEGORY)
    try:
        prefs = (await client._request("GET", "/api/v2/app/preferences")).json()
        record("env", "client", version=await client.version(), prefs={k: prefs.get(k) for k in PREFS})
        await scenario.run()
    finally:
        left = [h for h in scenario.hashes if await client.torrents_info([h])]
        if left:
            await client.delete(left, delete_files=False)
        await client._request("POST", "/api/v2/torrents/removeCategories", data={"categories": CATEGORY})
        await client.close()
        out.close()
        shutil.rmtree(folder, ignore_errors=True)
    print(f"raw: {raw.relative_to(ROOT)}")
    print("QBIT_BEHAVIOUR_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
