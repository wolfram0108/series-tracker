"""A check on the running service through its API, as the window does it (docs/deployment.md, «Проверка на живом сервисе»).

    .venv/bin/python tools/checks/live_api.py preview <address of a page> [--now]
    .venv/bin/python tools/checks/live_api.py sync <series id>
    .venv/bin/python tools/checks/live_api.py source <series id> <address of a page>
    .venv/bin/python tools/checks/live_api.py read <address of a page>

Signs in with ST_ADMIN_USER / ST_ADMIN_PASSWORD of .env (the values are never printed). Reads the table of the page as the
window of a new series reads it (`POST /api/sources/preview`), or at once whatever its age (`--now`: `POST
/api/sources/read`, the read a user asks for), waits for its end by the stream of events (`GET /api/stream`, no polling),
and prints the table: its state, the names, the players and the first lines with their heights. The requests the service
made to the hosts of the files meanwhile are counted in its log (`logs/info.log`, lines of httpx): `ranges` is how many
heads of segments it fetched, the cost of measuring frames. `sync` presses «Синхронизировать» of a series (`POST
/api/series/<id>/scan`), waits for its end as the window does (the status «scanning» of the series comes and goes in
`series_updated`) and prints the files of its plan and its failures. `source` adds a source to a series as the area of
its sources does: the table of the address is read as the window reads it, then the address becomes a source of the
series (`POST /api/series/<id>/sources`), and the sources of the series are printed. `read` only asks for the read of a
page now (`POST /api/sources/read`, the button «Перечитать») and does not wait for it: a read longer than the run of a
check is joined afterwards by `preview` (without `--now`), which waits for the end of the read under way. The last line
is `LIVE_API_DONE`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("ST_LIVE_BASE", "http://127.0.0.1:5000")
LOG = os.path.join(ROOT, "logs", "info.log")
# A read of a page and a sync end by themselves and are waited for by their event, however long they take (the limit of
# the whole check is its run's own). A stream that sends nothing at all for this long is a broken connection.
STREAM_SILENT_S = 170.0


async def lines_of(stream):
    """The lines of the stream of events; the stream going silent ends the check with an error."""
    lines = stream.aiter_lines()
    while True:
        try:
            yield await asyncio.wait_for(anext(lines), STREAM_SILENT_S)
        except StopAsyncIteration:
            return


def secrets() -> dict[str, str]:
    found: dict[str, str] = {}
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as env:
        for line in env:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.rstrip("\n").split("=", 1)
                found[key.strip()] = value
    return found


async def signed_in(http: httpx.AsyncClient) -> None:
    """The session of the administrator. Its cookie is Secure (the service is served behind HTTPS), so a client on plain
    HTTP would not send it back: it goes as a header. A failed sign-in is counted by the service and bans the address for
    a while: one attempt, and the reason is printed."""
    env = secrets()
    answer = await http.post("/api/login", json={"username": env["ST_ADMIN_USER"], "password": env["ST_ADMIN_PASSWORD"]})
    if answer.status_code != 200:
        sys.exit(f"sign-in refused: {answer.status_code} {answer.text[:200]}")
    http.cookies.clear()
    http.headers["Cookie"] = answer.headers["set-cookie"].split(";", 1)[0]


def ranges_since(moment: str) -> int:
    """Requests for a part of a file (206) the service made since the moment, by its log."""
    count = 0
    with open(LOG, encoding="utf-8", errors="replace") as log:
        for line in log:
            if '"group": "httpx"' in line and "206 Partial Content" in line and line[15:34] >= moment:
                count += 1
    return count


async def table_end(http: httpx.AsyncClient, canonical: str, listening: asyncio.Event) -> str:
    async with http.stream("GET", "/api/stream", params={"frames": "batch"}, timeout=None) as stream:
        listening.set()
        async for line in lines_of(stream):
            if line.startswith("data:"):
                for each in json.loads(line[5:]):
                    if each.get("event") in ("sources_table_ready", "sources_table_failed") \
                            and (each.get("data") or {}).get("url") == canonical:
                        return each["event"]
    return "stream closed"


async def preview(url: str, now: bool) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        await signed_in(http)
        resolved = (await http.post("/api/sources/resolve", json={"url": url})).json()
        canonical = resolved.get("canonical_url")
        print("site", resolved.get("site"), "canonical", canonical)
        if not canonical:
            sys.exit("no site reads this address")
        moment, started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), time.perf_counter()
        listening = asyncio.Event()
        ending = asyncio.create_task(table_end(http, canonical, listening))
        await listening.wait()
        if now:
            asked = await http.post("/api/sources/read", json={"url": url})
            state = "reading" if asked.status_code == 200 else f"refused {asked.status_code}"
        else:
            state = (await http.post("/api/sources/preview", json={"url": url, "refresh": True})).json().get("state")
        outcome = await ending if state == "reading" else state
        if not ending.done():
            ending.cancel()
        seconds = time.perf_counter() - started
        table = (await http.post("/api/sources/preview", json={"url": url, "refresh": False})).json()
        summary = table.get("summary") or {}
        print(f"read: {outcome} in {seconds:.1f} s; ranges asked {ranges_since(moment)}; table {table.get('state')},"
              f" read at {summary.get('read_at')}")
        print("names", table.get("names"))
        print("players", [(each.get("player"), each.get("rows")) for each in summary.get("players") or []],
              "unread", summary.get("unread"))
        for line in table.get("lines", [])[:5]:
            print("  ", line.get("key"), line.get("line"), line.get("heights"))


async def series_end(http: httpx.AsyncClient, series_id: int, listening: asyncio.Event) -> list[str]:
    """The statuses of the series once its sync has come and gone."""
    seen = False
    async with http.stream("GET", "/api/stream", params={"frames": "batch"}, timeout=None) as stream:
        listening.set()
        async for line in lines_of(stream):
            if line.startswith("data:"):
                for each in json.loads(line[5:]):
                    data = each.get("data") or {}
                    if each.get("event") != "series_updated" or data.get("id") != series_id or "statuses" not in data:
                        continue
                    if "scanning" in data["statuses"]:
                        seen = True
                    elif seen:
                        return data["statuses"]
    return ["stream closed"]


async def sync(series_id: int) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        await signed_in(http)
        started, listening = time.perf_counter(), asyncio.Event()
        ending = asyncio.create_task(series_end(http, series_id, listening))
        await listening.wait()
        pressed = await http.post(f"/api/series/{series_id}/scan")
        print("sync", pressed.status_code, pressed.text[:200])
        statuses = await ending
        plan = (await http.get(f"/api/series/{series_id}/plan")).json()
        print(f"ended in {time.perf_counter() - started:.1f} s, statuses {statuses}")
        for asset in plan.get("assets") or []:
            print("  file", asset.get("name"), [(track.get("group"), track.get("state")) for track in asset.get("tracks") or []])
        for failure in plan.get("failures") or []:
            print("  failure", json.dumps(failure, ensure_ascii=False)[:300])


async def source(series_id: int, url: str) -> None:
    await preview(url, False)
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        await signed_in(http)
        resolved = (await http.post("/api/sources/resolve", json={"url": url})).json()
        added = await http.post(f"/api/series/{series_id}/sources",
                                json={"url": resolved["canonical_url"], "source_type": resolved.get("source_type") or "web_video"})
        print("added:", added.status_code, added.text[:200])
        for each in (await http.get(f"/api/series/{series_id}/sources")).json():
            print("  source", each.get("id"), each.get("position"), each.get("url"), "profile", each.get("parser_profile_id"))


async def read(url: str) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        await signed_in(http)
        asked = await http.post("/api/sources/read", json={"url": url})
        print("read asked:", asked.status_code, asked.text[:120])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    shown = commands.add_parser("preview", help="the table of a page, as the window reads it")
    shown.add_argument("url")
    shown.add_argument("--now", action="store_true", help="read it now, whatever the age of its table")
    synced = commands.add_parser("sync", help="a sync of a series to its end, and the files of its plan")
    synced.add_argument("series_id", type=int)
    added = commands.add_parser("source", help="a source added to a series, as the area of its sources adds it")
    added.add_argument("series_id", type=int)
    added.add_argument("url")
    asked = commands.add_parser("read", help="the read of a page asked now and not waited for")
    asked.add_argument("url")
    args = parser.parse_args()
    asyncio.run(preview(args.url, args.now) if args.command == "preview"
                else source(args.series_id, args.url) if args.command == "source"
                else read(args.url) if args.command == "read" else sync(args.series_id))
    print("LIVE_API_DONE")


if __name__ == "__main__":
    main()
