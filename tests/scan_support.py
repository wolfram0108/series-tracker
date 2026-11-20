"""Fake neighbours of the scan module for a series of kind `video`, each answering as its contract says.

One module `World` stands for everything scan asks or tells: the tables of the pages (`sources`), the composition step,
the rules and voice-overs it snapshots, the folder move, the planner that does the work of a sync, and the torrent queue
and settings a pass reads. It records the order in which scan used it, so that a test proves the steps follow one
another by signals, and it ends a read of a page only when the test says so, so that a test waits for events and never
for time.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.module import BaseModule
from core.contracts import (CompositionRow, ReleaseTable, RowState, TableFailed, TableGetReply, TableRefresh,
                            TableReady, TableState)
from core.reasons import reason
from schema_db import migrated

READ_AT = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).isoformat()
# A test that waits longer than this for an event is stuck; the event is the signal, the number only ends a broken test.
STUCK_S = 5.0


def migrated_db(path: Path) -> str:
    """A database at the head of the migrations with the one profile the sources of the tests name."""
    import sqlite3
    migrated(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'профиль')")
        conn.commit()
    return str(path)


def row(series_id: int, source_id: int, key: str, season: int | None, start: int | None, end: int | None = None, *,
        part: int | None = None, player: str = "vk", voice: str = "Ruchime", height: int | None = 1080,
        state: RowState = RowState.PLAYABLE, link: str | None = None, published: str | None = "2026-10-20",
        text: str | None = None, excluded: bool = False, name: str | None = None) -> CompositionRow:
    """A row of the layout as `composition` gives it; `end` defaults to `start` (a single episode).

    `voice` is the label of the voice-over (a group, or "Неизвестная озвучка N"), `name` the name as it came: the
    label of a group by default, and nothing for a voice-over that is in no group."""
    unknown = voice.startswith("Неизвестная")
    return CompositionRow(
        series_id=series_id, source_id=source_id, row_key=key, excluded=excluded, season=season, episode_start=start,
        episode_end=start if end is None else end, part=part, voice_label=voice,
        voice_name=name if name is not None else ("" if unknown else voice),
        voice_group=None if unknown else voice, player=player,
        text=text if text is not None else f"Серия {start}", heights=[height] if height else [], state=state,
        published=published, link=link if link is not None else f"https://v.test/{player}/{key}")


@dataclass
class Page:
    """How the page answers a request that asks for a read.

    `state` is the state of the reply; `held` is the last complete table that goes with `reading` and `failed`;
    `event_first` makes the verdict reach the bus before the reply to the request does."""

    state: TableState = TableState.READY
    held: bool = True
    why: str | None = None
    event_first: bool = False


class World(BaseModule):
    name = "world"

    def __init__(self, bus) -> None:
        self.order: list[str] = []
        self.pages: dict[str, Page] = {}
        self.blocked: dict[str, str] = {}
        self.recompute_error: str | None = None
        self.settings_values: dict[str, str] = {}
        # Whether each executor has work of the series when a run asks (`<owner>.work.get`); none by default.
        self.work: dict[str, bool] = {}
        # The episodes each executor works on, as its states name them (WorkState.busy).
        self.busy: dict[str, list[list]] = {}
        # The number of the latest state an executor counted: events and replies carry it.
        self.seq = 0
        # An executor whose reply to `<owner>.work.get` waits for this gate (the answer is counted when it was asked).
        self.work_gates: dict[str, asyncio.Event] = {}
        # The stored configuration the owners give a run (RunSnapshot), and what every step of the run was given.
        self.profiles_export: dict = {}
        self.voices_export: dict = {}
        self.payloads: dict[str, list[dict]] = {}
        # The folder a save planned for the series (library), made by the first step of a sync, and its failure.
        self.planned_path: str | None = None
        self.move_error: str | None = None
        # One token per request for a table: the signal that scan has asked.
        self.asked: asyncio.Queue[str] = asyncio.Queue()
        # One token per sync handed to the planner: the signal that scan has sent it.
        self.dispatched: asyncio.Queue[str] = asyncio.Queue()
        super().__init__(bus)

    def register(self) -> None:
        h = self.handle
        h("library.relocation.apply", self.on_relocation_apply)
        h("planner.sync", self.on_planner_sync)
        h("planner.work.get", self.on_work_get)
        h("composition.recompute", self.on_recompute)
        h("rules.profiles.export", self.on_profiles_export)
        h("voices.settings.export", self.on_voices_export)
        h("sources.table.refresh", self.on_table)
        h("sources.pages.check", self.on_pages_check)
        h("sources.table.cancel", self.on_table_cancel)
        h("planner.sync.cancel", self.on_planner_cancel)
        h("torrents.queue.get", self.on_torrent_queue)
        h("settings.value.get", self.on_setting_get)
        h("settings.value.set", self.on_setting_set)

    # --- what scan asks ------------------------------------------------------------------------

    async def on_relocation_apply(self, env) -> dict:
        self.order.append(f"library.relocation.apply {env.payload['series_id']}")
        if self.move_error:
            raise RuntimeError(self.move_error)
        if self.planned_path is None:
            return {"moved": False}
        await self.request("catalog.series.set_save_path", {"series_id": env.payload["series_id"],
                                                            "save_path": self.planned_path})
        self.planned_path = None
        return {"moved": True}

    async def on_planner_sync(self, env) -> dict:
        """The planner writes the sync down and starts it; its work is told by work.series.state."""
        self.order.append(f"{env.topic} {env.payload['series_id']}")
        self.payloads.setdefault(env.topic, []).append(dict(env.payload))
        self.dispatched.put_nowait(env.topic)
        return {"accepted": True}

    async def on_table_cancel(self, env) -> dict:
        """A read of the page that goes stops, failed; with none going there is nothing to stop."""
        url = env.payload["url"]
        self.order.append(f"table.cancel {url}")
        page = self.pages.get(url)
        if page is None or page.state is not TableState.READING:
            return {"cancelled": False}
        self.pages[url] = Page(TableState.FAILED, held=page.held, why='{"code":"read_cancelled"}')
        return {"cancelled": True}

    async def on_planner_cancel(self, env) -> dict:
        """The planner stops the sync it runs: its work ends, and it says so."""
        self.order.append(f"{env.topic} {env.payload['series_id']}")
        return {"cancelled": False}

    async def on_work_get(self, env) -> dict:
        owner = env.topic.split(".")[0]
        self.order.append(f"{env.topic} {env.payload['series_id']}")
        counted = {"owner": owner, "series_id": env.payload["series_id"], "active": self.work.get(owner, False),
                   "busy": self.busy.get(owner, []), "seq": self.seq}
        gate = self.work_gates.get(owner)
        if gate is not None:
            await gate.wait()
        return counted

    def tell_work(self, series_id: int, owner: str, active: bool, busy: list[list] | None = None,
                  failure: str | None = None) -> None:
        """An executor's state changed: a later count than any reply given before."""
        self.seq += 1
        self.work[owner] = active
        self.busy[owner] = busy if busy is not None else []
        self.publish_event("work.series.state", {"owner": owner, "series_id": series_id, "active": active,
                                                 "busy": self.busy[owner], "seq": self.seq, "failure": failure})

    async def on_profiles_export(self, env) -> dict:
        return {"profiles": {key: value for key, value in self.profiles_export.items()
                             if key in env.payload["profile_ids"]}}

    async def on_voices_export(self, env) -> dict:
        return {"values": dict(self.voices_export)}

    async def on_recompute(self, env) -> dict:
        self.order.append("composition.recompute")
        self.payloads.setdefault(env.topic, []).append(dict(env.payload))
        if self.recompute_error is not None:
            raise ValueError(self.recompute_error)
        return {"changed": False}

    async def on_table(self, env) -> dict:
        request = TableRefresh.model_validate(env.payload)
        page = self.pages.setdefault(request.url, Page())
        self.order.append(f"sources.table.refresh {request.url}")
        table = ReleaseTable(url=request.url, site="ruchime", complete=True, read_at=READ_AT) if page.held else None
        reply = TableGetReply(url=request.url, state=page.state, table=table, reason=page.why)
        if page.event_first:
            if page.why:
                self.fail(request.url, page.why)
            else:
                self.finish(request.url)
        self.asked.put_nowait(request.url)
        return reply.model_dump(mode="json")

    async def on_pages_check(self, env) -> dict:
        """The pages `sources` says it cannot read now for want of a provider: `blocked` maps a page to its reason."""
        return {"blocked": [{"url": url, "reason": self.blocked[url]} for url in env.payload["urls"] if url in self.blocked]}

    async def on_torrent_queue(self, env) -> dict:
        return {"count": 0}

    async def on_setting_get(self, env) -> dict:
        return {"key": env.payload["key"], "value": self.settings_values.get(env.payload["key"])}

    async def on_setting_set(self, env) -> dict:
        self.settings_values[env.payload["key"]] = env.payload["value"]
        return env.payload

    # --- what the neighbours do on their own ------------------------------------------------------

    def finish(self, url: str) -> None:
        self.order.append(f"table.ready {url}")
        self.publish_event("sources.table.ready", TableReady(url=url, read_at=READ_AT).model_dump())

    def fail(self, url: str, why: str) -> None:
        self.order.append(f"table.failed {url}")
        self.publish_event("sources.table.failed", TableFailed(url=url, reason=why).model_dump())

    def failure(self, topic: str = "sources.table.refresh") -> str:
        return reason("composition_request_failed", topic=topic)
