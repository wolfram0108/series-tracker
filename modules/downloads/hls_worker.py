"""The process that downloads the segments of an HLS playlist (modules/downloads/hls_native.py), run apart so that it is
stopped as one: the native HLS downloader of yt-dlp over the playlist saved on disk, its fragments at once, AES-128
decrypted, an unavailable fragment never skipped. A fragment is taken from where its transport stream begins: some hosts
put the head of a picture before it.

A segment the host says is not there (404, 410) is told apart from every other failure (`gone`): it is the host's last
word on the file, and nothing else the downloader gives up on is.

stdin: one JSON line {url, target, headers, fragments}; stdout: JSON lines {type: progress | transferred | failed | error |
gone, ...}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .failure import GONE

TS_PACKET = 188
SYNC = 0x47
# Packets in a row that start with the sync byte: the start of a transport stream, not a byte of a picture before it.
SYNC_RUN = 5


def transport_stream(data: bytes) -> bytes:
    """The fragment from its first packet of a transport stream; the fragment as it is when it holds no run of packets
    (an fMP4 segment)."""
    position = data.find(bytes([SYNC]))
    while 0 <= position < len(data) - TS_PACKET * (SYNC_RUN - 1):
        if all(data[position + TS_PACKET * step] == SYNC for step in range(SYNC_RUN)):
            return data[position:]
        position = data.find(bytes([SYNC]), position + 1)
    return data


def run() -> int:
    from yt_dlp import YoutubeDL
    from yt_dlp.downloader.hls import HlsFD

    class CleanHlsFD(HlsFD):
        def _append_fragment(self, ctx, frag_content):
            return super()._append_fragment(ctx, transport_stream(frag_content))

        def report_retry(self, err, count, retries, *args, **kwargs):
            # A refusal of a fragment by its status comes here as the error itself (downloader/fragment.py); what the
            # downloader of a fragment gives up on by itself (a cut read, a 5xx) does not, and is no last word.
            if getattr(err, "status", None) in GONE:
                print(json.dumps({"type": "gone", "status": err.status}), flush=True)
            return super().report_retry(err, count, retries, *args, **kwargs)

    class Quiet:
        def debug(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            print(json.dumps({"type": "error", "message": str(message)[:300]}), flush=True)

    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    spec = json.loads(sys.stdin.readline())

    def progress(data: dict) -> None:
        print(json.dumps({"type": "progress", "downloaded_bytes": data.get("downloaded_bytes"),
                          "speed": data.get("speed"), "fragment_index": data.get("fragment_index"),
                          "fragment_count": data.get("fragment_count")}), flush=True)

    params = {"quiet": True, "noprogress": True, "logger": Quiet(), "progress_hooks": [progress],
              "continuedl": True, "overwrites": False, "enable_file_urls": True,
              "concurrent_fragment_downloads": spec["fragments"], "skip_unavailable_fragments": False,
              "retries": 3, "fragment_retries": 3, "socket_timeout": 15, "http_headers": spec["headers"]}
    info = {"id": "stream", "title": "stream", "url": spec["url"], "ext": "ts", "protocol": "m3u8_native",
            "http_headers": spec["headers"]}
    try:
        with YoutubeDL(params) as ydl:
            engine = CleanHlsFD(ydl, params)
            # A downloader made by hand is not given the hooks of the options: its progress is told only to its own.
            engine.add_progress_hook(progress)
            ok, _ = engine.download(spec["target"], info)
        if not ok:
            raise RuntimeError("the downloader of yt-dlp said the download failed")
        print(json.dumps({"type": "transferred", "bytes": Path(spec["target"]).stat().st_size}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - the parent gets the failure as a line, the process ends with its code
        print(json.dumps({"type": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
