"""Fake neighbours of the composition module, each a module on the real bus: the catalog, the sources, the rules
constructor and the voice-over groups. They answer as their contracts say and signal what they were asked, so that
a test waits for events and never for time.

`FakeRules` reads the text of a line the way a real profile of the stage would: the last field of the line that has no
code (`CODE:value`) is the text of the row; "Серия 3" and "3 серия" are an episode, "1-3 серия" a range, "Трейлер" is excluded, "Сезон 2" or "2 сезон"
anywhere in the line is the season (else the season of the card), and "[Имя]" in the text, else the field `V:` of the line, tags the voice-over.
"""
import asyncio
import itertools
import re
from datetime import datetime, timezone
from typing import Callable

from core import BaseModule
from core.contracts import (GroupsResolveReply, GroupsResolveRequest, ReleaseTable, RuleResult, RulesApplyReply,
                            RulesApplyRequest, TableFailed, TableGetReply, TableGetRequest, TableReady, TableState,
                            VoiceSettingsView)
from core.reasons import reason
from core.voice_names import fold_name
from modules.rules.numbering import season_of, settle_parts

HOST = "https://fake.test"
PAGE = f"{HOST}/p/1"
OTHER_PAGE = f"{HOST}/p/2"


def canonical(url: str) -> str:
    return url.split("?", 1)[0]


def source(url: str = PAGE, **fields) -> dict:
    """A source of a series as the catalog gives it; a web video source read by the profile 7 unless said otherwise."""
    return {"url": url, "source_type": "web_video", "parser_profile_id": 7,
            "vibix_season": None, "players_off": [], **fields}


class FakeCatalog(BaseModule):
    name = "catalog"

    def __init__(self, bus) -> None:
        self.series: dict[int, dict] = {}
        self._series_ids = itertools.count(1)
        self._source_ids = itertools.count(1)
        # One token per request for a card: the first thing every recount does, so the signal that one began.
        self.gets: asyncio.Queue[int] = asyncio.Queue()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("catalog.series.get", self.on_get)
        self.handle("catalog.series.list", self.on_list)
        self.handle("catalog.sources.by_profile", self.on_by_profile)

    def _card(self, series_id: int) -> dict:
        card = self.series[series_id]
        return {"id": series_id, "season": card["season"],
                "sources": sorted(card["sources"], key=lambda each: (each["position"], each["id"]))}

    async def on_get(self, env) -> dict:
        series_id = env.payload["series_id"]
        self.gets.put_nowait(series_id)
        if series_id not in self.series:
            raise LookupError(f"сериал {series_id} не найден")
        return self._card(series_id)

    async def on_list(self, env) -> list[dict]:
        return [self._card(series_id) for series_id in self.series]

    async def on_by_profile(self, env) -> list[dict]:
        return [each for card in self.series.values() for each in card["sources"]
                if each["parser_profile_id"] == env.payload["profile_id"]]

    # --- what the user does, as the catalog would announce it ------------------------------------

    def add_series(self, sources: list[dict] | None = None, season=None) -> int:
        series_id = next(self._series_ids)
        self.series[series_id] = {"season": season, "sources": []}
        self.publish_event("series.added", {"id": series_id})
        for fields in sources if sources is not None else [source()]:
            self.add_source(series_id, **fields)
        return series_id

    def add_source(self, series_id: int, **fields) -> int:
        sources = self.series[series_id]["sources"]
        made = {"id": next(self._source_ids), "series_id": series_id, "position": len(sources), **source(**{
            name: value for name, value in fields.items() if name != "position"})}
        made["position"] = fields.get("position", made["position"])
        sources.append(made)
        self.publish_event("series.source.added", {"series_id": series_id, "source_id": made["id"], "source": dict(made)})
        return made["id"]

    def find(self, source_id: int) -> dict:
        return next(each for card in self.series.values() for each in card["sources"] if each["id"] == source_id)

    def update_source(self, source_id: int, **fields) -> None:
        found = self.find(source_id)
        found.update(fields)
        self.publish_event("series.source.updated", {"series_id": found["series_id"], "source_id": source_id,
                                                     "source": dict(found), "changed": sorted(fields)})

    def remove_source(self, source_id: int) -> None:
        found = self.find(source_id)
        self.series[found["series_id"]]["sources"].remove(found)
        self.publish_event("series.source.removed", {"series_id": found["series_id"], "source_id": source_id,
                                                     "source": None})

    def set_season(self, series_id: int, season) -> None:
        self.series[series_id]["season"] = season
        self.publish_event("series.updated", {"series_id": series_id, "season": season, "statuses": [],
                                              "is_busy": False})

    def delete_series(self, series_id: int) -> None:
        del self.series[series_id]
        self.publish_event("series.deleted", {"series_id": series_id, "delete_from_qb": False})


class FakeSources(BaseModule):
    """The tables of pages, as the sources module answers: a read begins at a request that asks for one and ends when
    the test says so; a request that asks for no read says how the page stands, a failed read included."""

    name = "sources"

    def __init__(self, bus) -> None:
        self.tables: dict[str, ReleaseTable] = {}
        self.requests: list[TableGetRequest] = []
        self.reading: set[str] = set()
        self.verdicts: dict[str, str] = {}
        self.reads_begun: list[str] = []
        # A table older than its term: a request that asks for a read begins one, whatever is stored.
        self.stale = False
        # Pages whose read ends the moment after the reply to the request that began it.
        self.finish_at_once: dict[str, ReleaseTable] = {}
        # While held, an answer is made up and then waits: the events of the page can overtake it.
        self.gate = asyncio.Event()
        self.gate.set()
        self.asked: asyncio.Queue[str] = asyncio.Queue()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("sources.table.get", self.on_get, concurrent=True)

    def hold(self) -> None:
        while not self.asked.empty():
            self.asked.get_nowait()
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    async def on_get(self, env) -> dict:
        reply = self._answer(env)
        self.asked.put_nowait(reply["url"])
        await self.gate.wait()
        return reply

    def _answer(self, env) -> dict:
        request = TableGetRequest.model_validate(env.payload)
        self.requests.append(request)
        if not request.url.startswith(HOST):
            return TableGetReply(url=request.url, state=TableState.FAILED,
                                 reason=reason("site_not_found")).model_dump(mode="json")
        key = canonical(request.url)
        table, why = self.tables.get(key), None
        if not request.refresh:
            if key in self.reading:
                state = TableState.READING
            elif key in self.verdicts:
                state, why = TableState.FAILED, self.verdicts[key]
            else:
                state = TableState.READY if table is not None else TableState.ABSENT
        elif table is not None and not self.stale:
            state = TableState.READY
        else:
            state = TableState.READING
            if key not in self.reading:
                self.reading.add(key)
                self.reads_begun.append(key)
                self.verdicts.pop(key, None)
                if key in self.finish_at_once:
                    asyncio.get_running_loop().call_soon(self.finish, key, self.finish_at_once.pop(key))
        return TableGetReply(url=key, state=state, table=table, reason=why).model_dump(mode="json")

    # --- the end of a read ------------------------------------------------------------------------

    def finish(self, url: str, table: ReleaseTable, read_at: str | None = None) -> None:
        key = canonical(url)
        read_at = read_at or datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).isoformat()
        self.tables[key] = table.model_copy(update={"url": key, "complete": True, "read_at": read_at})
        self.reading.discard(key)
        self.verdicts.pop(key, None)
        self.publish_event("sources.table.ready", TableReady(url=key, read_at=read_at).model_dump())

    def fail(self, url: str, why: str) -> None:
        key = canonical(url)
        self.reading.discard(key)
        self.verdicts[key] = why
        self.publish_event("sources.table.failed", TableFailed(url=key, reason=why).model_dump())


Constructor = Callable[[str], dict]


def lecturer(text: str) -> dict:
    """What a profile of the stage extracts from a line; see the module docstring."""
    last = [field for field in text.split(" | ") if not re.match(r"[A-Z]+:", field)][-1]
    found: dict = {}
    if "Трейлер" in last:
        return {"excluded": True}
    if span := re.search(r"(\d+)-(\d+) серия", last):
        found.update(start=int(span[1]), end=int(span[2]))
    elif single := re.search(r"Серия (\d+)|(\d+) серия", last):
        found.update(episode=int(single[1] or single[2]))
    if season := re.search(r"Сезон (\d+)|(\d+) сезон", text):
        found.update(season=int(season[1] or season[2]))
    # A rule tags the voice-over: by a name in brackets, or by the field of the voice, as the owner's profiles do.
    if voice := re.search(r"\[(.+?)\]", last):
        found.update(voiceover=voice[1])
    elif field := re.search(r"(?:^| \| )V:([^|]+?)(?: \| |$)", text):
        found.update(voiceover=field[1])
    return found


class FakeRules(BaseModule):
    """The constructor. `gate` holds every answer; `entered` has one token per request that arrived."""

    name = "rules"

    def __init__(self, bus) -> None:
        self.profiles: dict[int, Constructor] = {7: lecturer, 8: lecturer}
        self.calls: list[RulesApplyRequest] = []
        self.entered: asyncio.Queue[int] = asyncio.Queue()
        self.gate = asyncio.Event()
        self.gate.set()
        self.broken = False
        super().__init__(bus)

    def register(self) -> None:
        self.handle("rules.apply", self.on_apply, concurrent=True)

    def hold(self) -> None:
        """From now on every answer waits for `release`; `entered` tells when one is held."""
        while not self.entered.empty():
            self.entered.get_nowait()
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    async def on_apply(self, env) -> dict:
        request = RulesApplyRequest.model_validate(env.payload)
        self.calls.append(request)
        self.entered.put_nowait(request.profile_id)
        await self.gate.wait()
        if self.broken:
            raise RuntimeError("конструктор недоступен")
        extract = self.profiles[request.profile_id]
        results = {}
        for line in request.lines:
            found = extract(line.text)
            if not found.get("excluded"):
                found["season"] = season_of(request.series_season, found.get("season"))
            results[line.key] = RuleResult(**found)
        return RulesApplyReply(results=settle_parts(request.lines, results)).model_dump(mode="json")


class FakeVoices(BaseModule):
    """The groups of voice-overs: a name belongs to the group whose trigger it equals without case and spaces."""

    name = "voices"

    def __init__(self, bus) -> None:
        self.groups: dict[str, list[str]] = {}
        self.asked: list[list[str]] = []
        self.values: list[dict | None] = []  # the stored settings a request named (a run's snapshot), or None
        self.garbage = False
        self.priority = ["vk", "cvh", "kodik"]  # the order of players the queues are ranked by
        super().__init__(bus)

    def register(self) -> None:
        self.handle("voices.groups.resolve", self.on_resolve)
        self.handle("voices.settings.get", self.on_settings_get)

    async def on_settings_get(self, env) -> dict:
        return VoiceSettingsView(priority=self.priority, voice_groups=self.groups, default_priority=self.priority,
                                 rejected=[]).model_dump()

    async def on_resolve(self, env) -> dict:
        request = GroupsResolveRequest.model_validate(env.payload)
        names = request.names
        self.asked.append(names)
        self.values.append(request.values)
        if self.garbage:
            return {"groups": "not a mapping"}
        owner = {fold_name(trigger): group for group, triggers in self.groups.items() for trigger in triggers}
        return GroupsResolveReply(groups={name: owner.get(fold_name(name)) for name in names}).model_dump()

    def set_groups(self, groups: dict[str, list[str]]) -> None:
        self.groups = groups
        self.publish_event("voices.settings.changed", {})
