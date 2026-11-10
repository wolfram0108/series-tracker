"""What a video application really serves for the first, the central and the last episode of every line of a card.

One JSON line per episode, appended to `data/raw/app_lines_inventory__<stamp>__<app>-<card>.jsonl` (raw answers stay out
of git): the parser's answer, the host of the address, what the host serves at its head (status, type, first bytes),
for a playlist its variants and the frame of the head of its first segment, for a file what ffprobe reads of it with its
error text, and the time of each step. Nothing is downloaded whole; the requests are three parses and a few heads a line.

    .venv/bin/python tools/collectors/app_lines_inventory.py <app> <card>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.http_limits import bounded_client  # noqa: E402
from modules.sources.apps import profiles_of  # noqa: E402
from modules.sources.apps.app99 import App99Client, AppError  # noqa: E402
from modules.sources.players.hls import attributes, head_of, is_playlist  # noqa: E402
from modules.sources.players.http import USER_AGENT  # noqa: E402
from modules.sources.sites.apps.site import lines_of  # noqa: E402

PEEK = 4096


def environment() -> dict[str, str]:
    found = dict(os.environ)
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as env:
        for line in env:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.strip().split("=", 1)
                found.setdefault(key, value.strip().strip("'\""))
    return found


async def ffprobe(target: str, *args: str, data: bytes | None = None) -> dict:
    began = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", *args, "-show_entries", "stream=codec_type,width,height:format=duration,format_name",
        "-of", "json", target, stdin=asyncio.subprocess.PIPE if data else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(data), 40)
    except TimeoutError:
        process.kill()
        return {"code": None, "error": "timeout 40 s", "seconds": round(time.monotonic() - began, 2)}
    parsed = json.loads(out or b"{}") if process.returncode == 0 else {}
    video = [each for each in parsed.get("streams", []) if each.get("codec_type") == "video"]
    return {"code": process.returncode, "error": err.decode(errors="replace").strip()[-300:],
            "width": video[0].get("width") if video else None, "height": video[0].get("height") if video else None,
            "audio": sum(1 for each in parsed.get("streams", []) if each.get("codec_type") == "audio"),
            "duration": (parsed.get("format") or {}).get("duration"), "seconds": round(time.monotonic() - began, 2)}


async def head(http, address: str, headers: dict) -> dict:
    began = time.monotonic()
    try:
        async with http.stream("GET", address, headers={"User-Agent": USER_AGENT, **headers,
                                                         "Range": f"bytes=0-{PEEK - 1}"}) as response:
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= PEEK:
                    break
            return {"status": response.status_code, "final_host": urlsplit(str(response.url)).hostname,
                    "redirects": len(response.history), "type": response.headers.get("content-type"),
                    "range": response.headers.get("content-range"), "length": response.headers.get("content-length"),
                    "playlist": is_playlist(body[:PEEK].decode("utf-8", "replace")), "first": body[:16].hex(),
                    "text": body[:PEEK].decode("utf-8", "replace") if is_playlist(body[:PEEK].decode("utf-8", "replace")) else "",
                    "final_url": str(response.url), "seconds": round(time.monotonic() - began, 2)}
    except Exception as exc:  # noqa: BLE001 - the inventory records what failed
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "seconds": round(time.monotonic() - began, 2)}


async def playlist(http, got: dict, headers: dict) -> dict:
    """The variants of a playlist and the frame of the head of the first segment of the best one."""
    text, base = got["text"], got["final_url"]
    found: dict = {"variants": []}
    lines = text.splitlines()
    for line, uri in zip(lines, lines[1:]):
        if line.startswith("#EXT-X-STREAM-INF"):
            found["variants"].append({**attributes(line), "uri": urljoin(base, uri.strip())})
    if found["variants"]:
        best = max(found["variants"], key=lambda each: int(each.get("BANDWIDTH") or 0))
        media = await http.get(best["uri"], headers={"User-Agent": USER_AGENT, **headers})
        text, base = media.text, str(media.url)
        found["media_status"] = media.status_code
    first = head_of(text, base) if is_playlist(text) else None
    if first is None:
        found["segment"] = None
        return found
    found["method"] = first.method
    segment = await http.get(first.segment, headers={"User-Agent": USER_AGENT, **headers, "Range": "bytes=0-262143"})
    found["segment"] = {"status": segment.status_code, "host": urlsplit(first.segment).hostname,
                        "bytes": len(segment.content)}
    if segment.status_code in (200, 206) and first.method == "NONE":
        found["frame"] = await ffprobe("pipe:0", "-f", "mpegts", data=segment.content[:262144])
    return found


async def main(app: str, card: str) -> None:
    (profile,) = [each for each in profiles_of(environment()) if each.name == app]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(os.path.join(ROOT, "data", "raw"), exist_ok=True)
    path = os.path.join(ROOT, "data", "raw", f"app_lines_inventory__{stamp}__{app}-{card}.jsonl")
    async with bounded_client(timeout=20.0, follow_redirects=True) as http:
        client = App99Client(profile, lambda: http)
        detail = await client.detail(card)
        system = await client.system()
        players = {str(each.get("code") or key): each for key, each in (system.get("player") or {}).items()
                   if isinstance(each, dict)}
        parsers = [each.get("id") for each in system.get("parser_api") or [] if isinstance(each, dict)]
        with open(path, "a", encoding="utf-8") as raw:
            for code, items in lines_of(detail):
                player = players.get(code, {})
                allowed = [part.strip() for part in str(player.get("parseUrl") or "").split(",") if part.strip()]
                picks = {"first": items[0], "central": items[len(items) // 2], "last": items[-1]} if items else {}
                headers = json.loads(player.get("headers") or "{}") if isinstance(player.get("headers"), str) else {}
                for which, (place, label, token) in picks.items():
                    record = {"at": datetime.now(timezone.utc).isoformat(), "app": app, "card": card, "line": code,
                              "line_name": player.get("name"), "line_type": player.get("type"), "allowed": allowed,
                              "episodes": len(items), "which": which, "place": place, "label": label, "parses": []}
                    address = token if token.startswith(("http://", "https://")) else None
                    for parser in [each for each in parsers if not allowed or str(each) in allowed] if address is None else []:
                        began = time.monotonic()
                        try:
                            address = await client.parse(parser, token)
                            record["parses"].append({"parser": parser, "ok": True, "seconds": round(time.monotonic() - began, 2)})
                            break
                        except AppError as exc:
                            record["parses"].append({"parser": parser, "ok": False, "reason": exc.reason,
                                                     "seconds": round(time.monotonic() - began, 2)})
                    record["address"] = address
                    if address:
                        record["host"] = urlsplit(address).hostname
                        got = record["head"] = await head(http, address, headers)
                        if got.get("playlist"):
                            record["hls"] = await playlist(http, got, headers)
                            got.pop("text", None)
                        elif got.get("status") in (200, 206):
                            record["file"] = await ffprobe(address, "-user_agent", USER_AGENT)
                        else:
                            record["file"] = await ffprobe(address, "-user_agent", USER_AGENT)
                        got.pop("text", None)
                    raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                    raw.flush()
                    print(f"{code:<8} {which:<8} {label[:20]:<20} host={record.get('host')} "
                          f"head={record.get('head', {}).get('status') or record.get('head', {}).get('error')} "
                          f"hls={bool(record.get('hls'))} "
                          f"frame={(record.get('hls') or {}).get('frame', {}).get('height') or (record.get('file') or {}).get('height')}")
    print("raw:", path)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
    print("INVENTORY_DONE")
