"""qBittorrent behaviour on a release update, recorded from the real client (stage 15, step 0).

Builds small synthetic releases v1 and v2 in a probe folder, adds them to qBittorrent in the category `st-probe`
and records every observation: client state, per-file progress and priority, the files on disk with sizes and
mtimes. Nothing but the probe torrents and the probe folder is touched; both are removed at the end.

Usage: .venv/bin/python tools/collectors/qbit_behaviour.py --host-root /home/user/qbit/downloads --client-root /downloads
Writes data/raw/qbit_behaviour__<when>__probe.jsonl and prints QBIT_BEHAVIOUR_DONE.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.torrents.qbt_client import QbtClient  # noqa: E402

PIECE = 256 * 1024
CATEGORY = "st-probe"
CHECKING = {"checkingUP", "checkingDL", "checkingResumeData", "moving", "allocating", "metaDL"}
PREFS = ("save_path", "temp_path_enabled", "incomplete_files_ext", "preallocate_all", "add_stopped_enabled",
         "auto_tmm_enabled", "torrent_content_layout", "use_unwanted_folder", "recheck_completed_torrents")


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    keys = sorted(value, key=lambda k: k.encode() if isinstance(k, str) else k)
    return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in keys) + b"e"


def payload(seed: str, size: int) -> bytes:
    # Deterministic content: the same seed is the same file in both versions.
    out, block = bytearray(), hashlib.sha256(seed.encode()).digest()
    while len(out) < size:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:size])


def make_torrent(root: str, files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    """Multi-file v1 torrent without padding files (the corpus norm: 10 of 12574 have padding)."""
    data = b"".join(content for _, content in files)
    pieces = b"".join(hashlib.sha1(data[i:i + PIECE]).digest() for i in range(0, len(data), PIECE))
    info = {"name": root, "piece length": PIECE, "pieces": pieces,
            # Paths inside the info are relative to the root name, which is the folder on disk.
            "files": [{"length": len(c), "path": p.split("/")[1:]} for p, c in files]}
    meta = bencode({"info": info, "created by": "st-probe", "creation date": int(time.time())})
    return meta, hashlib.sha1(bencode(info)).hexdigest()


def disk(folder: Path) -> list[dict]:
    if not folder.exists():
        return []
    return [{"path": str(p.relative_to(folder)), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
            for p in sorted(folder.rglob("*")) if p.is_file()]


class Probe:
    def __init__(self, client: QbtClient, out, host: Path, client_root: str) -> None:
        self.client, self.out, self.host, self.client_root = client, out, host, client_root

    def record(self, scenario: str, step: str, **fields) -> None:
        row = {"scenario": scenario, "step": step, "at": datetime.now(timezone.utc).isoformat(), **fields}
        self.out.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.out.flush()

    async def observe(self, scenario: str, step: str, h: str | None) -> None:
        info = (await self.client.torrents_info([h]))[0] if h and await self.client.torrents_info([h]) else None
        files = await self.client.torrent_files(h) if info else None
        pieces = None
        if info:
            resp = await self.client._request("GET", "/api/v2/torrents/pieceStates", params={"hash": h})
            pieces = resp.json() if resp else None
        keep = ("state", "progress", "amount_left", "size", "total_size", "save_path", "content_path")
        self.record(scenario, step, hash=h,
                    info={k: info.get(k) for k in keep} if info else None,
                    files=[{k: f.get(k) for k in ("index", "name", "size", "progress", "priority", "piece_range")}
                           for f in files] if files else None,
                    pieces=pieces, disk=disk(self.host))

    async def settle(self, h: str, *, until, budget_s: float = 60.0) -> dict:
        """qBittorrent pushes nothing over WebAPI: its state is read until the awaited one (budget is a safeguard)."""
        deadline = time.monotonic() + budget_s
        while True:
            got = await self.client.torrents_info([h])
            if got and until(got[0]):
                return got[0]
            if time.monotonic() > deadline:
                raise TimeoutError(f"{h}: {got[0]['state'] if got else 'absent'}")
            await asyncio.sleep(0.05)

    async def add(self, meta: bytes, h: str) -> None:
        verdict = await self.client._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True,
            data={"savepath": self.client_root, "category": CATEGORY, "paused": "true", "stopped": "true"},
            files={"torrents": ("probe.torrent", meta, "application/x-bittorrent")})
        if verdict.status_code == 409:
            raise RuntimeError(f"{h} already in the client")
        await self.settle(h, until=lambda t: t["state"] not in CHECKING)

    async def recheck(self, scenario: str, h: str) -> None:
        before = (await self.client.torrents_info([h]))[0]["progress"]
        await self.client.recheck([h])
        # The end of a check is the check seen running or the progress moved; a small check may pass unseen
        # between two reads, then the unchanged progress is recorded as such instead of guessed.
        try:
            await self.settle(h, until=lambda t: t["state"] in CHECKING or t["progress"] != before, budget_s=10)
            await self.settle(h, until=lambda t: t["state"] not in CHECKING)
        except TimeoutError as exc:
            self.record(scenario, "recheck_unobserved", hash=h, detail=str(exc), progress_before=before)

    async def gone(self, h: str) -> None:
        deadline = time.monotonic() + 30
        while await self.client.torrents_info([h]):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{h} still listed")
            await asyncio.sleep(0.3)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-root", required=True)
    ap.add_argument("--client-root", required=True)
    args = ap.parse_args()
    env = dict(line.strip().split("=", 1) for line in open(".env") if "=" in line and not line.startswith("#"))
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"st-probe-{when}"
    host = Path(args.host_root) / name
    raw = Path("data/raw") / f"qbit_behaviour__{when}__probe.jsonl"
    client = QbtClient(env["ST_QBIT_URL"], env["ST_QBIT_USER"], env["ST_QBIT_PASS"])
    await client.login()
    hashes: list[str] = []
    try:
        with raw.open("w", encoding="utf-8") as out:
            probe = Probe(client, out, host, f"{args.client_root}/{name}")
            prefs = (await client._request("GET", "/api/v2/app/preferences")).json()
            probe.record("env", "client", version=await client.version(), prefs={k: prefs.get(k) for k in PREFS})

            sizes = {"E01": PIECE * 5 + 1000, "E02": PIECE * 4 + 7777, "E03": PIECE * 3 + 333,
                     "old": PIECE * 2 + 55, "snd": PIECE + 999}
            v1 = [("Show/E01.mkv", payload("E01", sizes["E01"])),
                  ("Show/E02.mkv", payload("E02", sizes["E02"])),
                  ("Show/old_name.mkv", payload("E04", sizes["old"])),
                  ("Show/Sound/E01.mka", payload("snd1", sizes["snd"]))]
            # v2: E01 and E02 unchanged, E03 inserted between them by order, old_name renamed to E04,
            # the external sound of E01 changed in place (same path and size, other content).
            v2 = [("Show/E01.mkv", payload("E01", sizes["E01"])),
                  ("Show/E02.mkv", payload("E02", sizes["E02"])),
                  ("Show/E03.mkv", payload("E03", sizes["E03"])),
                  ("Show/E04.mkv", payload("E04", sizes["old"])),
                  ("Show/Sound/E01.mka", payload("snd1-new", sizes["snd"]))]
            meta1, h1 = make_torrent("Show", v1)
            meta2, h2 = make_torrent("Show", v2)
            hashes += [h1, h2]
            probe.record("env", "layout", v1=[[p, len(c)] for p, c in v1], v2=[[p, len(c)] for p, c in v2],
                         piece=PIECE, h1=h1, h2=h2)

            # Scenario A: the complete v1 is on disk; the client is told of it stopped.
            host.mkdir(mode=0o777)
            os.chmod(host, 0o777)
            for rel, content in v1:
                target = host / rel
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o777)
                target.write_bytes(content)
                target.chmod(0o666)
                os.chmod(target.parent, 0o777)
            await probe.observe("A", "disk_before_add", None)
            await probe.add(meta1, h1)
            await probe.observe("A", "added_stopped", h1)
            await probe.recheck("A", h1)
            await probe.observe("A", "rechecked", h1)

            # Scenario B: the update. v1 leaves the client without files, v2 comes stopped on the same files.
            await client.delete([h1], delete_files=False)
            await probe.gone(h1)
            await probe.observe("B", "v1_deleted_keep_files", None)
            await probe.add(meta2, h2)
            await probe.observe("B", "v2_added_stopped", h2)
            await probe.recheck("B", h2)
            await probe.observe("B", "v2_rechecked", h2)

            # Scenario C: priority 0 for the new E03 while stopped, then a start with no peers anywhere.
            files = await client.torrent_files(h2)
            e03 = [f["index"] for f in files if f["name"].endswith("E03.mkv")]
            await client.set_file_priority(h2, e03, 0)
            await probe.observe("C", "e03_priority_0", h2)
            await client.resume([h2])
            await probe.settle(h2, until=lambda t: t["state"] not in CHECKING and not t["state"].startswith("stopped"))
            await probe.observe("C", "started", h2)
            await client.pause([h2])
            await probe.settle(h2, until=lambda t: t["state"].startswith("stopped"))
            await probe.observe("C", "stopped_again", h2)

            # Scenario D: deletion with files; the renamed-away old_name.mkv is not in v2.
            await client.delete([h2], delete_files=True)
            await probe.gone(h2)
            await probe.observe("D", "v2_deleted_with_files", None)
    finally:
        left = [h for h in hashes if await client.torrents_info([h])]
        if left:
            await client.delete(left, delete_files=False)
        await client._request("POST", "/api/v2/torrents/removeCategories", data={"categories": CATEGORY})
        await client.close()
        shutil.rmtree(host, ignore_errors=True)
    print(f"raw: {raw}")
    print("QBIT_BEHAVIOUR_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
