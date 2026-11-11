"""The pace of the table of a card of a video application, read by the service's own code: the site of the application,
the player of its lines with the registry of admitted lines (modules/sources/apps/admitted/<app>.json), the last `recent`
episodes of each line (`ReadContext.recent`, as the stand reads). Every row with the resolution its own episode was
measured at (or why not), every measure of a frame with its kind (file or playlist), host and seconds, the seconds of the
read and the rows per second.

One JSON line per row and one for the read, appended to `data/raw/app_table_rate__<stamp>__<app>-<card>.jsonl` (out of
git). The requests are the card, a parse and a head of each row read.

    .venv/bin/python tools/collectors/app_table_rate.py <app> <card> [recent] [after] [lines=a,b]   (recent 0: the whole card)

With `after` the read follows the table the service stored of the card (app.db, read only), as its next read would: the
rows that table measured keep their frames and only the others are asked. With `lines=` only the named lines are read,
whatever the registry says of them: the table part of the admission of a line (requirement 4а) is checked so before the
registry admits it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "collectors"))

from app_lines_inventory import environment  # noqa: E402
from core.contracts import TableRow  # noqa: E402
from core.http_limits import bounded_client  # noqa: E402
from modules.sources.apps import card_address, profiles_of  # noqa: E402
from modules.sources.apps.lines import LineRegistry  # noqa: E402
from modules.sources.module import APP_PROTOCOLS, CONNECTIONS  # noqa: E402
from modules.sources.players.app_lines import AppLines, AppPlayer  # noqa: E402
from modules.sources.sites import ReadContext  # noqa: E402
from modules.sources.sites.apps.site import AppSite  # noqa: E402


def stored_rows(url: str) -> tuple[TableRow, ...]:
    """The rows of the table the service stored of the card."""
    with sqlite3.connect(f"file:{os.path.join(ROOT, 'app.db')}?mode=ro", uri=True) as db:
        found = db.execute('select "key", player, text, heights, state, reason, link, site_fields from release_rows '
                           "where url = ? order by seq", (url,)).fetchall()
    return tuple(TableRow(key=key, player=player, text=text, heights=json.loads(heights), state=state, reason=why,
                          link=link, site_fields=json.loads(fields))
                 for key, player, text, heights, state, why, link, fields in found)


async def main(app: str, card: str, recent: int | None, after: bool, lines: list[str]) -> None:
    (profile,) = [each for each in profiles_of(environment()) if each.name == app]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = os.path.join(ROOT, "data", "raw", f"app_table_rate__{stamp}__{app}-{card}.jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    async with bounded_client(timeout=20.0, follow_redirects=True, limits=CONNECTIONS) as http:
        registry = LineRegistry(frozenset(lines), {}) if lines else LineRegistry.load(app)
        player = AppPlayer(APP_PROTOCOLS[profile.protocol](profile, lambda: http), registry=registry)
        site = AppSite(player, AppLines([player]))
        measures: list[dict] = []

        def timed(kind: str, inner):
            async def measure(address, *args, **kwargs):
                began = time.monotonic()
                answer = await inner(address, *args, **kwargs)
                reading = answer.reading
                measures.append({"kind": kind, "host": urlsplit(address).hostname, "seconds": round(time.monotonic() - began, 2),
                                 "heights": list(reading.resolutions) if reading else None,
                                 "said": (reading.message if reading else answer.error) or ""})
                return answer
            return measure

        def timed_head(inner):
            async def measure(http_, head, referer):
                began = time.monotonic()
                height = await inner(http_, head, referer)
                measures.append({"kind": "playlist", "host": urlsplit(head.segment).hostname,
                                 "seconds": round(time.monotonic() - began, 2), "heights": [height] if height else [],
                                 "said": "" if height else "the head of the first segment gave no frame"})
                return height
            return measure

        player._measure_file = timed("file", player._measure_file)
        player._measure = timed_head(player._measure)
        began = time.monotonic()
        last = stored_rows(card_address(app, card)) if after else ()
        draft = await site.read(card_address(app, card), ReadContext(http, lambda *_: None, recent=recent, last_rows=last))
        seconds = time.monotonic() - began
    with open(raw_path, "a", encoding="utf-8") as raw:
        for row in draft.rows:
            raw.write(json.dumps({"app": app, "card": card, "player": row.player, "text": row.text,
                                  "heights": row.heights, "state": str(row.state), "fields": row.site_fields},
                                 ensure_ascii=False) + "\n")
        for each in measures:
            raw.write(json.dumps({"app": app, "card": card, "measure": each}, ensure_ascii=False) + "\n")
        summary = {"app": app, "card": card, "recent": recent, "rows": len(draft.rows), "seconds": round(seconds, 2),
                   "rows_per_s": round(len(draft.rows) / seconds, 1) if seconds else None,
                   "kept": sum(1 for row in last if row.heights and row.site_fields.get("frame") == "measured"),
                   "unread": [part.model_dump(mode="json") for part in draft.unread],
                   "at": datetime.now(timezone.utc).isoformat()}
        raw.write(json.dumps(summary, ensure_ascii=False) + "\n")
    by_player: dict[str, list] = {}
    for row in draft.rows:
        by_player.setdefault(row.player, []).append(row.heights)
    for name, heights in by_player.items():
        print(f"{name:<18} rows={len(heights)} absent={sum(1 for each in heights if not each)} "
              f"heights={sorted({h for each in heights for h in (each or [None])}, key=str)}")
    causes: dict[tuple[str, str], int] = {}
    for row in draft.rows:
        if not row.heights:
            causes[(row.player, (row.reason or "")[:150])] = causes.get((row.player, (row.reason or "")[:150]), 0) + 1
    for (name, why), count in sorted(causes.items(), key=lambda each: -each[1]):
        print(f"  absent {count:>4} {name:<16} {why}")
    for each in sorted(measures, key=lambda each: -each["seconds"])[:12]:
        print(f"  measure {each['kind']:<8} {each['host'] or '':<34} {each['seconds']:6.1f} s {each['heights']} {each['said'][:80]}")
    print(f"{len(draft.rows)} rows in {seconds:.1f} s = {summary['rows_per_s']} rows/s, unread {len(draft.unread)}")
    print("raw:", raw_path)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], (int(sys.argv[3]) or None) if len(sys.argv) > 3 else 10,
                     "after" in sys.argv[4:],
                     next((each[6:].split(",") for each in sys.argv[4:] if each.startswith("lines=")), [])))
    print("RATE_DONE")
