"""The file lists of a directory of .torrent files, as raw material for the analysis of how releases lay out their files.

One JSON line per .torrent: {torrent, root, files: [{path, size}], error}. The list is read by the service's own reader
(`modules.torrents.infohash.torrent_file_list`), so the paths are the ones qBittorrent would show. A new file is written
for every run (data/raw is append-only); nothing is read from the network.

    python tools/collectors/torrent_files.py <dir with .torrent> <label>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.torrents.infohash import TorrentParseError, torrent_file_list  # noqa: E402


def main() -> None:
    source, label = Path(sys.argv[1]), sys.argv[2]
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = ROOT / "data" / "raw" / f"torrent_files__{when}__{label}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = errors = 0
    # Torrents are read one at a time and written as they come: the archive holds thousands of them.
    with out.open("x", encoding="utf-8") as sink:
        for path in sorted(source.rglob("*.torrent")):
            record = {"torrent": str(path.relative_to(source)), "root": None, "files": [], "error": None}
            try:
                record["root"], files = torrent_file_list(path.read_bytes())
                record["files"] = [{"path": each["path"], "size": each["size"]} for each in files]
            except (TorrentParseError, OSError) as exc:
                record["error"] = str(exc)
                errors += 1
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    print(f"WROTE {out} torrents={count} errors={errors}")
    print("TORRENT_FILES_DONE")


if __name__ == "__main__":
    main()
