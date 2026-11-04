"""Fake neighbours of the scan module for a series of kind `video`, each answering as its contract says.

One module `World` stands for everything scan asks or tells: the tables of the pages (`sources`), the layout
(`composition`), the order of players (`voices`), the queue of downloads and the commands to the downloads and the
voice-overs. It records the order in which scan used it, so that a test proves the steps follow one another by signals,
and it ends a read of a page only when the test says so, so that a test waits for events and never for time.
"""
import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import BaseModule
from core.contracts import (CompositionChanged, CompositionReply, CompositionRow, EffectiveLever, EpisodeRef,
                            EpisodeSettings, Level, ReleaseTable, RowState, SourceStatus, SourceSummary, TableFailed,
                            TableGetReply, TableGetRequest, TableReady, TableState, VoiceSettingsView)
from core.reasons import reason

ROOT = Path(__file__).resolve().parent.parent
READ_AT = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).isoformat()
PRIORITY = ["vk", "allvideo", "vibix", "kodik", "cvh"]
# A test that waits longer than this for an event is stuck; the event is the signal, the number only ends a broken test.
STUCK_S = 5.0


def migrated_db(path: Path) -> str:
    """A database at the head of the migrations with the one profile the sources of the tests name."""
    import sqlite3
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=ROOT, check=True,
                   capture_output=True)
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


def lever(name: str, value, level: Level = Level.SERIES, target: str | None = None) -> EffectiveLever:
    return EffectiveLever(lever=name, target=target, value=value, level=level)


def episode_settings(season: int, episode: int, *levers: EffectiveLever) -> EpisodeSettings:
    return EpisodeSettings(season=season, episode=episode, levers=list(levers))


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
        self.options: list[dict] = []  # the options of every request for a table, in the order asked
        self.pages: dict[str, Page] = {}
        self.rows: list[CompositionRow] = []
        self.settings: list[EpisodeSettings] = []
        self.sources: list[tuple[int, str]] = []
        self.priority = list(PRIORITY)
        self.rev = 0
        self.recompute_error: str | None = None
        self.queue_tasks: list[dict] = []
        self.queue_error = False
        self.fs_sync_calls = 0
        self.reprocess_calls = 0
        self.settings_values = {"scan_interval_minutes": "60"}
        # One token per request for a table: the signal that scan has asked.
        self.asked: asyncio.Queue[str] = asyncio.Queue()
        # One token per command to download or to take the voice-overs: the signal that scan has sent it.
        self.dispatched: asyncio.Queue[str] = asyncio.Queue()
        super().__init__(bus)

    def register(self) -> None:
        h = self.handle
        h("renaming.reprocess", self.on_reprocess)
        h("downloads.fs.sync", self.on_fs_sync)
        h("downloads.queue.get", self.on_queue)
        h("downloads.dispatch", self.on_dispatch)
        h("voices.dispatch", self.on_dispatch)
        h("voices.settings.get", self.on_voice_settings)
        h("composition.get", self.on_layout)
        h("composition.recompute", self.on_recompute)
        h("sources.table.get", self.on_table)
        h("torrents.queue.get", self.on_torrent_queue)
        h("settings.value.get", self.on_setting_get)
        h("settings.value.set", self.on_setting_set)

    # --- what scan asks ------------------------------------------------------------------------

    async def on_reprocess(self, env) -> dict:
        self.reprocess_calls += 1
        self.order.append("renaming.reprocess")
        return {"ok": True}

    async def on_fs_sync(self, env) -> dict:
        self.fs_sync_calls += 1
        self.order.append("downloads.fs.sync")
        return {"adopted": 0, "lost": 0}

    async def on_queue(self, env) -> dict:
        if self.queue_error:
            raise RuntimeError("очередь недоступна")
        return {"tasks": self.queue_tasks, "count": len(self.queue_tasks)}

    async def on_dispatch(self, env) -> None:
        self.order.append(f"{env.topic} {env.payload['series_id']}")
        self.dispatched.put_nowait(env.topic)

    async def on_voice_settings(self, env) -> dict:
        self.order.append("voices.settings.get")
        return VoiceSettingsView(priority=self.priority, voice_groups={}, default_priority=PRIORITY,
                                 rejected=[]).model_dump()

    async def on_layout(self, env) -> dict:
        self.order.append("composition.get")
        series_id = env.payload["series_id"]
        sources = [SourceSummary(source_id=source_id, url=url, status=SourceStatus.READY)
                   for source_id, url in self.sources]
        return CompositionReply(series_id=series_id, rev=self.rev, rows=self.rows, sources=sources,
                                settings=self.settings).model_dump(mode="json")

    async def on_recompute(self, env) -> dict:
        self.order.append("composition.recompute")
        if self.recompute_error is not None:
            raise ValueError(self.recompute_error)
        return {"rev": self.rev, "changed": False}

    async def on_table(self, env) -> dict:
        request = TableGetRequest.model_validate(env.payload)
        page = self.pages.setdefault(request.url, Page())
        self.order.append(f"sources.table.get {request.url}")
        self.options.append(dict(request.options))
        table = ReleaseTable(url=request.url, site="ruchime", complete=True, read_at=READ_AT) if page.held else None
        reply = TableGetReply(url=request.url, state=page.state, table=table, reason=page.why)
        if page.event_first:
            if page.why:
                self.fail(request.url, page.why)
            else:
                self.finish(request.url)
        self.asked.put_nowait(request.url)
        return reply.model_dump(mode="json")

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

    def changed(self, series_id: int, *episodes: tuple[int, int], summary_only: bool = False) -> None:
        self.rev += 1
        self.publish_event("composition.changed", CompositionChanged(
            series_id=series_id, rev=self.rev, summary_only=summary_only,
            episodes=[EpisodeRef(season=season, episode=episode) for season, episode in episodes]).model_dump())

    def failure(self, topic: str = "sources.table.get") -> str:
        return reason("composition_request_failed", topic=topic)
