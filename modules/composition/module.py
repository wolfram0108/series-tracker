"""Module composition — the layout of a series with several sources, and the output settings that go with it (stage 10).

The layout is the rows of the tables of all sources of a series, each with the numbers the constructor of rules gave it
and the group of its voice-over. The module owns the tables `composition_rows`, `composition_state`, `composition_sources`,
`unknown_voices` and `output_settings`. It reads no site, picks no reference and downloads nothing. Torrent sources of a series are not its business.

What a handler does is: read what the neighbours and its own tables say, count it with the pure functions of `calc/layout`
(the projection in `project.py`, the levers in `levers.py`, their inheritance in `settings.py`, the queues in `queues.py`, the
steps of a count in `recount.py`, of a command in `edit.py`, the replies in `reply.py` and `preview.py`), write, and tell what
changed. A count that needs an answer of a neighbour is made in steps, with the question between them. Every topic the module
handles, asks or tells is a record of `core.topics`.

The replies of the queries (`CompositionReply`, `CompositionPreviewReply`, `QueuesReply`) go as the models themselves, immutable,
as they are in the process; the gateway writes them for the window. `CompositionReply` is written with the fields of its part only.

Compilations (stage 12, `calc/layout/compilations.py`): a row whose video holds several episodes is a compilation, named by its key in
domain terms (group, source, player, season, range). It is a level of the levers between a season and an episode
(`output_settings.compilation`): an episode inherits from the compilation it stands in. An episode stands in at most one: the
user's word (`compilation.member`: a key, or "" for none) while that compilation still takes it, else the shortest
compilation that covers it, is switched on (`compilation.take`) and is higher than its best single video. Settings set
`compilation.member` to a compilation that is no higher are refused (`compilation_not_better`).

Queries:
  composition.get {series_id, include_excluded?, sources_only?, part?, episodes?} → CompositionReply (`composition.layout
      {series_id}` is the same answer for the planner, which asks for the whole layout): the rows of the layout in the order of the sources
      (rows the constructor excluded, gave no episode number, the source's own choice left out — a player switched off
      for it, another season of a catalog, `exclusion='source'` —, or whose player is switched off in the settings for
      every source, `exclusion='settings'`; only with include_excluded), the summary of every
      source (status waiting / no_profile / ready / failed, players with rows, seasons, resolutions and voice-overs
      with their groups, `place`: the site and the channel its rows come from, as the line of the constructor names them), the levers of the series and of each season of the layout as the headers of those levels
      show them (`levels`, the series first), and the levers of every episode of the layout with the level each comes
      from, with the compilation it stands in (`EpisodeSettings.compilation`), and the compilations of every season with
      the episodes each holds and why the others are not in it (`compilations`). A series not counted yet has revision 0
      and nothing else.
  composition.recompute {series_id} → {rev, changed}: counts the series now, with the tables as they are stored (no
      read of a site), and answers when a count that began no earlier than the request has ended, its record is
      written and its event is out. `changed` tells whether that count published. A count already running is
      not the answer: the request sets the flag and waits for the next one. Refused with ValueError(reason) when a
      neighbour did not answer (the same reason goes out as composition.failed), with LookupError for a series the
      catalog no longer has.
  composition.preview {url?, profile_id?, series_season?, refresh?, lines?, players_off?, vibix_season?}
      → CompositionPreviewReply {url, state, reason?, summary?, lines, rows}: an address that is no source of a series yet, as the add window and the test of rules
      show it. The table is asked of `sources.table.get` (refresh true begins a read, false reads nothing); `state`
      and `reason` are the sources' own. From the last complete table, when there is one (also while it is being read
      or the last read failed): `summary` is the summary of a source made by the same code as for a series (no
      profile: no_profile; a profile: ready; a failed read: failed), `lines` are the rows as the constructor gets
      them, each with its answer when `profile_id` is given (`series_season` is the fallback season); `rows` are the rows of
      the layout the profile makes of them (the same code as for a series, source_id 0, without the rows the constructor
      left out), made with the choices of the source (`players_off`, `vibix_season`): the only data a
      window draws episodes and voice-overs of a draft from. Without a table `summary` is empty and so are `lines`, `rows`. Nothing is written and no source is made; a neighbour that does not answer
      refuses the request with ValueError(reason). With `lines` (typed, or the file names of a release) no table is
      asked: the reply is `ready`, has no summary, and its lines are those given (key = their place in the list),
      each with the answer of the profile when one is given.
  composition.queues.get {series_id, season?, episode?} → QueuesReply: the queue of the reference and of the voice-over of
      each group of a level, in the order the core tries their variants (`core.variant_queue`), with the value and the
      level of every lever of the level that acts on them (`calc/layout/queues.py`). The order of players is asked of voices. Also the
      queue of the markup reference (stage 14) and `markup_follows`: with no `markup.order` on the chain of levels it is
      the queue of the reference itself. The variants the user declared equal (Р-99, the levers `reference.same` and
      `markup.same`: lists of groups of variants) stand in the queue as one place, and a row of a group carries its number
      (`QueueRow.same`, by where the group first stands) and the level the groups come from (`same_level`).
  composition.settings.set {series_id, season?, episode?, lever, target?, value} → {ok}: sets the value of a lever
      at a level. Refused with ValueError(reason) — `unknown_lever`, `bad_target` or `bad_value` — and nothing is stored.
      The first edit of the markup reference on a level that follows the reference writes the queue shown (the order of
      the reference, the variants it leaves out and the groups of equal variants it shows) with the edit, in one write.
  composition.settings.set_many {series_id, items: [SettingsSet…]} → {ok}: the same, for several levers at once (the
      boxes the user ticked in a queue, all of them off): applied in order, one write, one revision, one
      `composition.changed`, so the plan of the series is counted once for all of them. A refused item refuses the whole
      command and nothing is stored. `composition.settings.set` is the command of one item.
  composition.settings.reset {series_id, season?, episode?, lever?, target?, queue?} → {ok}: the levers of the level
      (or the one named, or those of the queue named) go back to the level above. Refused the same way when the lever or
      the target is not the registry's.

Events (outgoing):
  composition.series.reading (SeriesReadProgress) — how far the read of a page of a source of a series is, per series and
      source, with the rate, whoever asked for the read (a sync, the user, the term): the owner of the layout knows which
      series own the page. `active` false ends it (read or failed), the other fields as it last had them.
  composition.changed {series_id, rev, episodes, summary_only} — the layout, its summaries or the settings changed.
      `rev` grows with every publication; a consumer acts on the latest. `episodes` are the episodes touched (by the
      rows that differ, by the settings that came out differently); empty means the whole series. `summary_only` is
      set when no row and no lever of any episode changed, only what a window shows (the summaries of the sources —
      their time of reading alone is no change —, the headers of the levels): a consumer of rows and levers skips it,
      a window refreshes. A recount that changes nothing publishes nothing.
  composition.failed {series_id, reason} — a recount could not be done (a neighbour did not answer): the previous layout
      stays in force. Reasons: `composition_request_failed` {topic}, `composition_error`.
  series.purged {series_id, owner: composition} — the report to catalog after series.deleted.

Events (incoming) — every one of them only asks for a recount of the series it concerns:
  series.source.added / .updated / .removed {series_id, source_id} — the source began, changed or ended; the table of
      an added or changed source is asked for with a read (the site is not read again within the term of the table)
  sources.table.progress (TableProgress) — how far a read is: told on as composition.series.reading to the series that
      own the page
  sources.table.ready {url, read_at} / sources.table.failed {url, reason} — the sources of ours that read that page,
      found by the canonical address `sources.table.get` returned for them. How a page stands, a failed read
      included, is what `sources.table.get` says of it at the recount; nothing of it is kept here
  rules.profile.changed {profile_id} — the series whose sources are read by that profile
  voices.settings.changed — the groups of voice-overs or the players switched off in the settings may have changed:
      every series is counted again
  series.updated {series_id, season?} — the season of the card is the constructor's fallback season
  series.added {id} — a series came; series.deleted {series_id} — everything of the series goes, then series.purged
  Whatever the cause, a recount is the same one function over the whole series, and recounting twice with the same
  inputs changes nothing. A cause that comes while the series is being counted sets a flag and the series is counted
  once more when the running count ends; series are counted independently of each other.

Neighbours (all over the bus): catalog.series.get / .list, catalog.sources.by_profile, sources.table.get,
rules.apply (one request per source with a profile, all lines of its table at once), voices.settings.get (the order of
players, for the queues and for the markup copy of a command — once for all its items —, and the players switched off for
every source, at each count), voices.groups.resolve (all
names in one request). A source without a table or without a profile does not stop the others. Start this module
after the modules it asks; a request that gets no answer fails the recount, not the start.

Resumability: the layout, the numbers of unknown voice-overs and the settings are in the database before an event
goes out. At start every series is counted again, a series the catalog no longer has is
purged, and a failure there is logged, not raised.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

from calc.layout import edit, project, queues, settings
from calc.layout.preview import draft_source, lines_preview, lines_request, page_preview, tableless_preview
from calc.layout.project import SourceInput
from calc.layout.recount import derive, rules_request
from calc.layout.reply import layout_of
from calc.layout.stored import StoredSource
from core import compute
from core.contracts import (CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionPreviewReply,
                            CompositionPreviewRequest, CompositionReply, EpisodeRef, GroupsResolveRequest, QueuesGet,
                            QueuesReply, ReleaseTable, RulesApplyReply, RulesProfileChanged, RunChanged, RunSnapshot,
                            SeriesReadProgress, SeriesSource, SettingsReset, SettingsSet, SettingsSetMany, SourceEvent,
                            TableFailed, TableGetReply, TableGetRequest, TableProgress, TableReady, TableState, VoiceOrderSet,
                            VoiceSettingsView, VoiceTitleSet, availability)
from core.db import Database
from core.envelope import Envelope
from core.module import BaseModule
from core.reasons import reason
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_LIST, CATALOG_SOURCES_BY_PROFILE, COMPOSITION_CHANGED,
                         COMPOSITION_FAILED, COMPOSITION_GET, COMPOSITION_LAYOUT, COMPOSITION_PREVIEW,
                         COMPOSITION_QUEUES_GET, COMPOSITION_RECOMPUTE, COMPOSITION_SERIES_READING,
                         COMPOSITION_SETTINGS_RESET, COMPOSITION_SETTINGS_SET, COMPOSITION_SETTINGS_SET_MANY,
                         COMPOSITION_VOICE_ORDER_SET, COMPOSITION_VOICE_TITLE_SET, RULES_APPLY, RULES_PROFILE_CHANGED,
                         SCAN_RUN_CHANGED, SERIES_ADDED, SERIES_DELETED, SERIES_PURGED, SERIES_SOURCE_ADDED,
                         SERIES_SOURCE_REMOVED, SERIES_SOURCE_UPDATED, SERIES_UPDATED, SOURCES_TABLE_FAILED,
                         SOURCES_TABLE_GET, SOURCES_TABLE_PROGRESS, SOURCES_TABLE_READY, VOICES_GROUPS_RESOLVE,
                         VOICES_SETTINGS_CHANGED, VOICES_SETTINGS_GET, Topic)

from .repository import CompositionRepository

OWNER = "composition"
# The fields of a source that change what is read of its page: a change of any other is no new read.
READ_FIELDS = frozenset({"url", "players_off", "vibix_season"})


class _Unavailable(Exception):
    """A neighbour did not answer a request, or answered what the contract does not allow."""

    def __init__(self, topic: str) -> None:
        self.reason = reason("composition_request_failed", topic=topic)
        super().__init__(self.reason)


@dataclass
class _Outcome:
    """How a count of a series ended: the revision in force, whether the count published, or why it failed."""

    rev: int = 0
    changed: bool = False
    failure: str | None = None


@dataclass
class _Flight:
    """A series being counted. `dirty` asks for one more count after this one; `refresh` are the sources whose
    page is to be read; `pending` is the number of table requests sent whose answer is not yet noted; `started` is
    the number of counts begun, and a waiter is answered by the first count whose number is at least its own."""

    refresh: set[int]
    dirty: bool = False
    pending: int = 0
    started: int = 0
    waiters: list[tuple[int, asyncio.Future]] = field(default_factory=list, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)


class CompositionModule(BaseModule):
    name = "composition"

    def __init__(self, bus, db: Database) -> None:
        self.repo = CompositionRepository(db)
        self._flights: dict[int, _Flight] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._deleted: set[int] = set()
        # The series whose sync is under way (`scan.run.changed`), and the counts events asked for meanwhile.
        self._frozen: set[int] = set()
        # The configuration each open run works by (RunSnapshot): the counts of the series during the run read the rules
        # and the groups from it, not from their owners.
        self._snapshots: dict[int, RunSnapshot] = {}
        self._owed: set[int] = set()
        self._owed_refresh: dict[int, set[int]] = {}
        # The pages of our sources by canonical address, so that an event of `sources` finds them without a query.
        self._pages: dict[str, dict[int, int]] = {}
        self._page_of: dict[int, str] = {}
        # The reads told as under way, by series and source: the end of one is told with what it last had.
        self._reading: dict[tuple[int, int], SeriesReadProgress] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(COMPOSITION_GET, self.on_get)
        self.handle(COMPOSITION_LAYOUT, self.on_get)
        self.handle(COMPOSITION_QUEUES_GET, self.on_queues_get)
        # Waits for a whole count, so the requests for different series must not queue behind each other.
        self.handle(COMPOSITION_RECOMPUTE, self.on_recompute, concurrent=True)
        self.handle(COMPOSITION_PREVIEW, self.on_preview, concurrent=True)
        self.handle(COMPOSITION_SETTINGS_SET, self.on_settings_set)
        self.handle(COMPOSITION_SETTINGS_SET_MANY, self.on_settings_set_many)
        self.handle(COMPOSITION_SETTINGS_RESET, self.on_settings_reset)
        self.handle(SERIES_ADDED, self.on_series_added)
        self.handle(SERIES_UPDATED, self.on_series_updated)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        self.handle(SERIES_SOURCE_ADDED, self.on_source_changed)
        self.handle(SERIES_SOURCE_UPDATED, self.on_source_changed)
        self.handle(SERIES_SOURCE_REMOVED, self.on_source_changed)
        self.handle(SOURCES_TABLE_PROGRESS, self.on_table_progress)
        self.handle(SOURCES_TABLE_READY, self.on_table_ready)
        self.handle(SOURCES_TABLE_FAILED, self.on_table_failed)
        self.handle(RULES_PROFILE_CHANGED, self.on_profile_changed)
        self.handle(VOICES_SETTINGS_CHANGED, self.on_groups_changed)
        self.handle(SCAN_RUN_CHANGED, self.on_run_changed)
        self.handle(COMPOSITION_VOICE_ORDER_SET, self.on_voice_order_set)
        self.handle(COMPOSITION_VOICE_TITLE_SET, self.on_voice_title_set)

    # --- start ------------------------------------------------------------------------------

    async def on_start(self) -> None:
        try:
            for series_id, source_id, url in await self.repo.pages():
                self._remember(source_id, series_id, url)
            await self._resume()
        except Exception:  # noqa: BLE001 — the other duties of the module do not wait for the recounts
            self.log.exception("пересчёт раскладок при старте не начат")

    async def _resume(self) -> None:
        listing = await self._ask(CATALOG_SERIES_LIST, {})
        known = set(await self.repo.series_ids())
        present = {each["id"] for each in listing}
        for each in listing:
            if each["id"] in known or each.get("sources"):
                self._schedule(each["id"])
        for series_id in known - present:
            await self._purge(series_id)

    async def idle(self) -> None:
        """Returns when no series is being counted; the signal of completion for whoever stops or checks the module."""
        while self._flights:
            await asyncio.gather(*(flight.task for flight in list(self._flights.values()) if flight.task),
                                 return_exceptions=True)

    # --- queries and commands ------------------------------------------------------------------

    async def on_get(self, env: Envelope) -> CompositionReply:
        """The layout of a series, or the part of it asked, as the model itself: whoever asks in this process is given it
        as it is (the gateway writes it for the window, the planner reads it) and nobody changes it."""
        request = CompositionGetRequest.model_validate(env.payload)
        stored = await self.repo.stored(request.series_id)
        # The layout of a series is thousands of rows: the reply is made off the loop.
        return await compute.run(layout_of, request, stored)

    async def on_queues_get(self, env: Envelope) -> QueuesReply:
        """The queues of a level in the order the core tries their variants: the same rule, the same order of players and
        of sources, and the same levers the choice of the reference and the plan of voice-overs read."""
        request = QueuesGet.model_validate(env.payload)
        stored = await self.repo.stored(request.series_id)
        voices = await self._voices()
        # The queues walk the rows and the levers of the level: counted off the loop.
        return await compute.run(queues.reply_of, stored, voices, request)

    async def on_voice_order_set(self, env: Envelope) -> dict:
        """The user's order of the voice-overs of a season: the order of the tracks in its containers (ЭТАП_11). It is a
        plan: the containers follow it at the next sync."""
        command = VoiceOrderSet.model_validate(env.payload)
        self._require_editable(command.series_id)
        await self.request(CATALOG_SERIES_GET, {"series_id": command.series_id})
        async with self._lock(command.series_id):
            stored = await self.repo.stored(command.series_id)
            change = await compute.run(edit.reordered, stored, command)
            if change is None:
                return {"ok": True}
            rev = stored.rev + 1
            await self.repo.write(command.series_id, orders=change.orders, rev=rev)
            self._publish_changed(command.series_id, rev, change.touched, summary_only=False, part="levers")
        return {"ok": True}

    async def on_voice_title_set(self, env: Envelope) -> dict:
        """The user's name of the track of a voice-over group in every container of the series. It is a plan: the
        containers take it at the next sync (their tracks are copied into a file that bears the name)."""
        command = VoiceTitleSet.model_validate(env.payload)
        self._require_editable(command.series_id)
        await self.request(CATALOG_SERIES_GET, {"series_id": command.series_id})
        async with self._lock(command.series_id):
            stored = await self.repo.stored(command.series_id)
            change = await compute.run(edit.renamed, stored, command)
            if change is None:
                return {"ok": True}
            rev = stored.rev + 1
            await self.repo.write(command.series_id, titles=change.titles, rev=rev)
            self._publish_changed(command.series_id, rev, change.touched, summary_only=False, part="levers")
        return {"ok": True}

    async def on_preview(self, env: Envelope) -> CompositionPreviewReply:
        """An address that is no source of a series yet, as the add window and the test of rules show it. It reads
        the table, builds the summary and the lines of the constructor by the same code as for a series, and writes
        nothing: no row, no source, no revision."""
        request = CompositionPreviewRequest.model_validate(env.payload)
        if request.lines is not None:
            return await self._preview_lines(request)
        try:
            got = await self._ask(SOURCES_TABLE_GET, TableGetRequest(
                url=request.url, refresh=request.refresh).model_dump(mode="json"))
            table = got.table if got.table is not None and got.table.complete else None
            if table is None:
                return tableless_preview(got)
            source = draft_source(request)
            off = await self._players_off()
            answer = None
            if request.profile_id is not None:
                answer = await self._answer(source, table, request.series_season, everywhere=off)
            item = SourceInput(source, table, answer, got.reason if got.state == TableState.FAILED else None,
                               got.url, players_off=off)
            names = await compute.run(project.voice_names, [item])
            groups = await self._groups(names) if names else {}
        except _Unavailable as exc:
            raise ValueError(exc.reason) from exc
        # The page is thousands of rows: its projection and the reply are made off the loop.
        return await compute.run(page_preview, got, item, groups)

    async def _preview_lines(self, request: CompositionPreviewRequest) -> CompositionPreviewReply:
        """Lines the window holds itself: no page and no table, only the profile's answer to each line."""
        # The lines a window holds can be thousands: the question and the reply are made off the loop.
        payload = await compute.run(lines_request, request)
        results = {}
        if payload is not None:
            try:
                results = (await self._ask(RULES_APPLY, payload)).results
            except _Unavailable as exc:
                raise ValueError(exc.reason) from exc
        return await compute.run(lines_preview, request, results)

    async def on_recompute(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        if series_id in self._deleted:
            raise LookupError(reason("series_not_found", series_id=series_id))
        if env.payload.get("snapshot") is not None:
            self._snapshots[series_id] = RunSnapshot.model_validate(env.payload["snapshot"])
        self._schedule(series_id, ordered=True)
        flight = self._flights[series_id]
        answer: asyncio.Future = asyncio.get_running_loop().create_future()
        # The count that is running began before this request; the one it is made to run again is the first to begin after.
        flight.waiters.append((flight.started + 1, answer))
        outcome: _Outcome = await answer
        if series_id in self._deleted:
            raise LookupError(reason("series_not_found", series_id=series_id))
        if outcome.failure is not None:
            raise ValueError(outcome.failure)
        return {"rev": outcome.rev, "changed": outcome.changed}

    async def on_settings_set(self, env: Envelope) -> dict:
        return await self._set([SettingsSet.model_validate(env.payload)])

    async def on_settings_set_many(self, env: Envelope) -> dict:
        return await self._set(SettingsSetMany.model_validate(env.payload).items)

    async def _set(self, items: list[SettingsSet]) -> dict:
        """The one way a lever is set, one item or many: every item is checked before anything is written, then all of
        them are applied in order in one write, so that the count that follows is one."""
        for record in items:
            problem = settings.validate(record)
            if problem is not None:
                raise ValueError(problem)
        series_id = items[0].series_id
        self._require_editable(series_id)
        stored = await self.repo.stored(series_id) if edit.reads_layout(items) else None
        if stored is not None:
            await compute.run(edit.checked, stored, items)
        writes = items
        if any(record.lever in edit.MARKUP_LEVERS for record in items):
            # The queue of the markup reference is made by the order of players, asked once for all the items.
            voices = await self._voices()
            writes = await compute.run(edit.writes_of, stored, items, voices)
        return await self._edit(series_id, edit.with_writes, writes)

    def _require_editable(self, series_id: int) -> None:
        """A lever of a series whose sync is under way is not changed: the sync works by what it found (the table of
        what is allowed, as every owner of the series refuses)."""
        availability.require(availability.Action.EDIT,
                             availability.SERIES_SCANNING if series_id in self._frozen else availability.SERIES_IDLE)

    async def on_settings_reset(self, env: Envelope) -> dict:
        scope = SettingsReset.model_validate(env.payload)
        problem = settings.validate_reset(scope)
        if problem is not None:
            raise ValueError(problem)
        self._require_editable(scope.series_id)
        return await self._edit(scope.series_id, edit.with_reset, scope)

    async def _edit(self, series_id: int, change, argument) -> dict:
        """Applies a command to the records of the series: `change(stored, argument)` is the pure step of `calc.layout.edit`
        that makes the records after it and what it touches."""
        # A command about a series the catalog does not have is refused with the catalog's own answer.
        await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
        async with self._lock(series_id):
            if series_id in self._deleted:
                raise LookupError(reason("series_not_found", series_id=series_id))
            stored = await self.repo.stored(series_id)
            # The levers of every episode counted twice is tenths of a second over a big layout: off the loop.
            made = await compute.run(change, stored, argument)
            if not made.changed:
                return {"ok": True}
            rev = stored.rev + 1 if made.shown else None
            await self.repo.write(series_id, records=made.records, rev=rev)
            if made.shown:
                self._publish_changed(series_id, rev, made.touched, summary_only=not made.touched, part="levers")
        return {"ok": True}

    # --- events -----------------------------------------------------------------------------

    def on_series_added(self, env: Envelope) -> None:
        # A series number is never given twice, but a test or a restored database may; an earlier delete must not stick.
        self._deleted.discard(env.payload["id"])

    def on_series_updated(self, env: Envelope) -> None:
        if "season" in env.payload:
            self._schedule(env.payload["series_id"])

    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._deleted.add(series_id)
        await self._purge(series_id)
        self.publish_event(SERIES_PURGED, {"series_id": series_id, "owner": OWNER})

    def on_source_changed(self, env: Envelope) -> None:
        """A source came, went or changed: the layout is counted again. Its page is read anew only when it is new or an
        update changed what is read of it; any other field (its profile, its place, its colour) needs no read."""
        event = SourceEvent.model_validate(env.payload)
        fresh = env.topic == SERIES_SOURCE_ADDED.name or (
            env.topic == SERIES_SOURCE_UPDATED.name and bool(set(event.changed) & READ_FIELDS))
        self._schedule(event.series_id, (event.source_id,) if fresh else ())

    def on_table_progress(self, env: Envelope) -> None:
        """How far a read is goes to every series that has a source on that page; a page of no series is nobody's news."""
        packet = TableProgress.model_validate(env.payload)
        for source_id, series_id in self._pages.get(packet.url, {}).items():
            told = SeriesReadProgress(series_id=series_id, source_id=source_id, url=packet.url, phase=packet.phase,
                                      current=packet.current, total=packet.total, rate=packet.rate)
            self._reading[(series_id, source_id)] = told
            self.publish_event(COMPOSITION_SERIES_READING, told.model_dump(mode="json"))

    def _read_ended(self, url: str) -> None:
        """The read of a page ended (read or failed): the series that were told of it are told it is over."""
        for source_id, series_id in self._pages.get(url, {}).items():
            last = self._reading.pop((series_id, source_id), None)
            if last is not None:
                self.publish_event(COMPOSITION_SERIES_READING,
                                   last.model_copy(update={"active": False}).model_dump(mode="json"))

    def on_table_ready(self, env: Envelope) -> None:
        url = TableReady.model_validate(env.payload).url
        self._read_ended(url)
        self._wake(url)

    def on_table_failed(self, env: Envelope) -> None:
        # The verdict is in `sources` before the event goes out; the recount reads it there and asks for no new read.
        url = TableFailed.model_validate(env.payload).url
        self._read_ended(url)
        self._wake(url)

    async def on_profile_changed(self, env: Envelope) -> None:
        profile_id = RulesProfileChanged.model_validate(env.payload).profile_id
        try:
            sources = await self._ask(CATALOG_SOURCES_BY_PROFILE, {"profile_id": profile_id})
        except _Unavailable:
            return  # logged by the request; no series is known to tell the failure about
        for series_id in sorted({each["series_id"] for each in sources}):
            self._schedule(series_id)

    async def on_groups_changed(self, env: Envelope) -> None:
        for series_id in await self.repo.series_ids():
            self._schedule(series_id)

    def _wake(self, url: str) -> None:
        """A page was read or failed: count the series of ours that read it. An event of a page nobody has noted
        yet belongs to a count that has asked for it and not yet been answered, so that count is made to run again."""
        for series_id in sorted(set(self._pages.get(url, {}).values())):
            self._schedule(series_id)
        for flight in self._flights.values():
            if flight.pending:
                flight.dirty = True

    # --- the count of a series -----------------------------------------------------------------

    def on_run_changed(self, env: Envelope) -> None:
        """A sync of the series opened or closed its run. While it is open the layout stays as the sync counted it (the
        snapshot); what an event asked for meanwhile is counted once the run is closed."""
        run = RunChanged.model_validate(env.payload)
        if run.active:
            self._frozen.add(run.series_id)
            return
        self._frozen.discard(run.series_id)
        self._snapshots.pop(run.series_id, None)
        if run.series_id in self._owed:
            self._owed.discard(run.series_id)
            self._schedule(run.series_id, self._owed_refresh.pop(run.series_id, set()))

    def _schedule(self, series_id: int, refresh: Iterable[int] = (), *, ordered: bool = False) -> None:
        """`ordered` is the count the sync itself asks for (composition.recompute); any other count of a series whose
        run is open waits for the end of the run."""
        if series_id in self._deleted:
            return
        if series_id in self._frozen and not ordered:
            self._owed.add(series_id)
            self._owed_refresh.setdefault(series_id, set()).update(refresh)
            return
        flight = self._flights.get(series_id)
        if flight is not None:
            flight.dirty = True
            flight.refresh.update(refresh)
            return
        flight = _Flight(refresh=set(refresh))
        self._flights[series_id] = flight
        flight.task = self.spawn(self._run(series_id, flight), name=f"composition:{series_id}")

    async def _run(self, series_id: int, flight: _Flight) -> None:
        try:
            while True:
                flight.dirty = False
                refresh, flight.refresh = flight.refresh, set()
                flight.started += 1
                self._answer_waiters(flight, await self._recount(series_id, flight, refresh))
                if not flight.dirty or series_id in self._deleted:
                    return
        finally:
            if self._flights.get(series_id) is flight:
                del self._flights[series_id]
            # Whoever is still waiting asked for a count that will not come: the series is gone or the module stops.
            for _, answer in flight.waiters:
                if answer.done():
                    continue
                if series_id in self._deleted:
                    answer.set_exception(LookupError(reason("series_not_found", series_id=series_id)))
                else:
                    answer.cancel()
            flight.waiters.clear()

    @staticmethod
    def _answer_waiters(flight: _Flight, outcome: _Outcome) -> None:
        """A count has ended: those who asked before it began are answered, the rest wait for a later one."""
        waiting = []
        for number, answer in flight.waiters:
            if number <= flight.started:
                if not answer.done():
                    answer.set_result(outcome)
            else:
                waiting.append((number, answer))
        flight.waiters[:] = waiting

    async def _recount(self, series_id: int, flight: _Flight, refresh: set[int]) -> _Outcome:
        try:
            inputs, groups = await self._gather(series_id, flight, refresh)
            return await self._settle(series_id, inputs, groups)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed count is a signal, not the end of the module
            if series_id in self._deleted:
                return _Outcome()
            if isinstance(exc, _Unavailable):
                failure = exc.reason
            else:
                self.log.exception("пересчёт раскладки сериала %d не удался", series_id)
                failure = reason("composition_error")
            self.publish_event(COMPOSITION_FAILED,
                               CompositionFailed(series_id=series_id, reason=failure).model_dump(mode="json"))
            return _Outcome(failure=failure)

    async def _gather(self, series_id: int, flight: _Flight, refresh: set[int]
                      ) -> tuple[list[SourceInput], dict[str, str | None]]:
        """What the neighbours say about the series, in the steps of the count: the card, the players switched off, then for
        every source its table and the answer of its rules, then the groups of the voice-overs of all of them (the names the
        groups are asked about are counted from the answers of the rules); nothing is written here."""
        card = await self._ask(CATALOG_SERIES_GET, {"series_id": series_id})
        sources = [SeriesSource.model_validate(each) for each in card.get("sources", [])]
        snapshot = self._snapshots.get(series_id)
        off = await self._players_off(snapshot)
        inputs = []
        for source in sources:
            table, failure, url = await self._table(series_id, flight, source, source.id in refresh)
            answer = None
            if table is not None and source.parser_profile_id is not None:
                answer = await self._answer(source, table, card.get("season"), snapshot, off)
            inputs.append(SourceInput(source, table, answer, failure, url, players_off=off))
        names = await compute.run(project.voice_names, inputs)
        return inputs, await self._groups(names, snapshot) if names else {}

    async def _table(self, series_id: int, flight: _Flight, source: SeriesSource, begin: bool
                     ) -> tuple[ReleaseTable | None, str | None, str]:
        flight.pending += 1
        try:
            reply = await self._table_reply(source, begin)
            self._remember(source.id, series_id, reply.url)
            if reply.state == TableState.ABSENT and not begin:
                # Nothing was ever read for the page and no read failed: the one read is begun here, then the events
                # carry it on. A page whose last read failed is `failed`, not `absent`, and a recount never reads it.
                reply = await self._table_reply(source, True)
                self._remember(source.id, series_id, reply.url)
        finally:
            flight.pending -= 1
        table = reply.table if reply.table is not None and reply.table.complete else None
        return table, reply.reason if reply.state == TableState.FAILED else None, reply.url

    async def _table_reply(self, source: SeriesSource, begin: bool) -> TableGetReply:
        request = TableGetRequest(url=source.url, refresh=begin, rows=source.rows_per_line)
        return await self._ask(SOURCES_TABLE_GET, request.model_dump(mode="json"))

    async def _players_off(self, snapshot: RunSnapshot | None = None) -> frozenset[str]:
        """The players switched off in the settings for every source, as they are now or as the run took them."""
        values = snapshot.voices if snapshot is not None else None
        view = await self._ask(VOICES_SETTINGS_GET, {"values": values})
        return frozenset(view.players_off)

    async def _answer(self, source: SeriesSource, table: ReleaseTable, season,
                      snapshot: RunSnapshot | None = None, everywhere: frozenset[str] = frozenset()) -> RulesApplyReply:
        rules = snapshot.profiles.get(source.parser_profile_id) if snapshot is not None else None
        payload = await compute.run(rules_request, source, table, season, rules, everywhere)
        if payload is None:
            return RulesApplyReply(results={})
        return await self._ask(RULES_APPLY, payload)

    async def _groups(self, names: list[str], snapshot: RunSnapshot | None = None) -> dict[str, str | None]:
        values = snapshot.voices if snapshot is not None else None
        reply = await self._ask(VOICES_GROUPS_RESOLVE, GroupsResolveRequest(names=names, values=values).model_dump())
        return reply.groups

    async def _settle(self, series_id: int, inputs: list[SourceInput], groups: dict[str, str | None]) -> _Outcome:
        """Projects, compares with what is stored, writes in one transaction and tells what changed."""
        async with self._lock(series_id):
            if series_id in self._deleted:
                return _Outcome()
            stored = await self.repo.stored(series_id)
            if not inputs and not stored.sources and not stored.rows and not stored.records:
                return _Outcome(rev=stored.rev)
            # The projection and its comparison with what is stored are pure work over thousands of rows: off the loop.
            derived = await compute.run(derive, inputs, groups, stored)
            for group in derived.conflicts:
                self.log.info("сериал %d: у слитых групп расходящиеся настройки %s — выбор возвращён уровню выше",
                              series_id, group[0].lever)
            result = derived.result
            sources = [StoredSource(item.source.id, item.source.position, self._page_of.get(item.source.id), summary)
                       for item, summary in zip(sorted(inputs, key=lambda i: (i.source.position, i.source.id)),
                                                result.sources)]
            for source_id in derived.gone:
                self._unmap(source_id)
            layout_changed = derived.rows_changed or bool(derived.moved)
            publish = layout_changed or derived.summaries_changed or derived.headers_changed
            rev = stored.rev + 1 if publish else None
            parts = dict(
                rows=result.rows if derived.rows_changed else None,
                subtitles=result.subtitles if derived.rows_changed else None,
                sources=sources if sources != stored.sources else None,
                numbering=result.unknown_voices if result.unknown_voices != stored.numbering else None,
                records=derived.records if derived.records_changed else None, rev=rev,
                orders=derived.orders if derived.orders != stored.orders else None)
            if any(value is not None for value in parts.values()):
                await self.repo.write(series_id, **parts)
            if not publish:
                return _Outcome(rev=stored.rev)
            if layout_changed:
                touched = derived.touched
                whole = touched is None or not (touched | derived.moved)
                self._publish_changed(series_id, rev, set() if whole else touched | derived.moved,
                                      part="whole" if derived.rows_changed else "levers")
            else:
                self._publish_changed(series_id, rev, set(), summary_only=True,
                                      part="levers" if derived.headers_changed else "sources")
            return _Outcome(rev=rev, changed=True)

    def _publish_changed(self, series_id: int, rev: int, episodes: set[tuple[int, int]], *,
                         summary_only: bool = False, part: str = "whole") -> None:
        changed = CompositionChanged(series_id=series_id, rev=rev, summary_only=summary_only, part=part, episodes=[
            EpisodeRef(season=season, episode=episode) for season, episode in sorted(episodes)])
        self.publish_event(COMPOSITION_CHANGED, changed.model_dump(mode="json"))

    async def _purge(self, series_id: int) -> None:
        """Takes the series away after the count that may be running for it has ended."""
        flight = self._flights.get(series_id)
        if flight is not None and flight.task is not None:
            await asyncio.gather(flight.task, return_exceptions=True)
        async with self._lock(series_id):
            for source_id in await self.repo.purge(series_id):
                self._unmap(source_id)
        self._locks.pop(series_id, None)

    # --- helpers ----------------------------------------------------------------------------

    def _lock(self, series_id: int) -> asyncio.Lock:
        return self._locks.setdefault(series_id, asyncio.Lock())

    def _remember(self, source_id: int, series_id: int, url: str) -> None:
        old = self._page_of.get(source_id)
        if old is not None and old != url:
            self._pages.get(old, {}).pop(source_id, None)
        self._page_of[source_id] = url
        self._pages.setdefault(url, {})[source_id] = series_id

    def _unmap(self, source_id: int) -> None:
        for key in [each for each in self._reading if each[1] == source_id]:
            self._reading.pop(key)
        url = self._page_of.pop(source_id, None)
        if url is not None:
            self._pages.get(url, {}).pop(source_id, None)

    async def _voices(self) -> VoiceSettingsView:
        """The settings of the voice-overs as they stand now: the order of players and sources the queues are made by."""
        return await self._reply_of(VOICES_SETTINGS_GET, {})

    async def _reply_of(self, topic: Topic, payload=None):
        """The answer of the owner of `topic` as the model of its record. An answer that came as that model is taken as it
        is; a dictionary is checked, off the loop (the tables and the answers of the rules are thousands of rows)."""
        answer = await self.request(topic, payload)
        if topic.reply is None or isinstance(answer, topic.reply):
            return answer
        return await compute.run(topic.reply.model_validate, answer)

    async def _ask(self, topic: Topic, payload=None):
        """`_reply_of` for a count: any failure of a neighbour is one signal, that the count did not happen."""
        try:
            return await self._reply_of(topic, payload)
        except Exception as exc:  # noqa: BLE001
            self.log.error("запрос %s не выполнен: %s: %s", topic.name, type(exc).__name__, exc)
            raise _Unavailable(topic.name) from exc
