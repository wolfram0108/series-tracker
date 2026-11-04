"""Module composition — the layout of a series with several sources, and the output settings that go with it (stage 10).

The layout is the rows of the tables of all sources of a series, each with the numbers the constructor of rules gave it
and the group of its voice-over; the pure rules are in `project.py`, the levers in `levers.py`, their inheritance in
`settings.py`. The module owns the tables `composition_rows`, `composition_state`, `composition_sources`,
`unknown_voices` and `output_settings`. It reads no site, picks no reference and downloads nothing. Torrent sources of a series are not its business.

Queries:
  composition.get {series_id, include_excluded?} → CompositionReply: the rows of the layout in the order of the sources
      (rows the constructor excluded, gave no episode number, or the source's own choice left out — a switched-off
      player, another season of a catalog, `exclusion='source'` — only with include_excluded), the summary of every
      source (status waiting / no_profile / ready / failed, players with rows, seasons, resolutions and voice-overs
      with their groups), the levers of the series and of each season of the layout as the headers of those levels
      show them (`levels`, the series first), and the levers of every episode of the layout with the level each comes
      from. A series not counted yet has revision 0 and nothing else.
  composition.recompute {series_id} → {rev, changed}: counts the series now, with the tables as they are stored (no
      read of a site), and answers when a count that began no earlier than the request has ended, its record is
      written and its event is out. `changed` tells whether that count published. A count already running is
      not the answer: the request sets the flag and waits for the next one. Refused with ValueError(reason) when a
      neighbour did not answer (the same reason goes out as composition.failed), with LookupError for a series the
      catalog no longer has.
  composition.preview {url?, options?, profile_id?, series_season?, refresh?, lines?, players_off?, vibix_season?, voice_name?}
      → CompositionPreviewReply {url, state, reason?, summary?, lines, rows}: an address that is no source of a series yet, as the add window and the test of rules
      show it. The table is asked of `sources.table.get` (refresh true begins a read, false reads nothing); `state`
      and `reason` are the sources' own. From the last complete table, when there is one (also while it is being read
      or the last read failed): `summary` is the summary of a source made by the same code as for a series (no
      profile: no_profile; a profile: ready; a failed read: failed), `lines` are the rows as the constructor gets
      them, each with its answer when `profile_id` is given (`series_season` is the fallback season); `rows` are the rows of
      the layout the profile makes of them (the same code as for a series, source_id 0, without the rows the constructor
      left out), made with the choices of the source (`players_off`, `vibix_season`, `voice_name`): the only data a
      window draws episodes and voice-overs of a draft from. Without a table `summary` is empty and so are `lines`, `rows`. Nothing is written and no source is made; a neighbour that does not answer
      refuses the request with ValueError(reason). With `lines` (typed, or the file names of a release) no table is
      asked: the reply is `ready`, has no summary, and its lines are those given (key = their place in the list),
      each with the answer of the profile when one is given.
  composition.settings.set {series_id, season?, episode?, lever, target?, value} → {ok}: sets the value of a lever
      at a level. Refused with ValueError(reason) — `unknown_lever`, `bad_target` or `bad_value` — and nothing is stored.
  composition.settings.reset {series_id, season?, episode?, lever?, target?} → {ok}: the levers of the level (or the
      one named) go back to the level above. Refused the same way when the lever or the target is not the registry's.

Events (outgoing):
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
  sources.table.ready {url, read_at} / sources.table.failed {url, reason} — the sources of ours that read that page,
      found by the canonical address `sources.table.get` returned for them. How a page stands, a failed read
      included, is what `sources.table.get` says of it at the recount; nothing of it is kept here
  rules.profile.changed {profile_id} — the series whose sources are read by that profile
  voices.settings.changed — the groups of voice-overs may have changed: every series is counted again
  series.updated {series_id, season?} — the season of the card is the constructor's fallback season
  series.added {id} — a series came; series.deleted {series_id} — everything of the series goes, then series.purged
  Whatever the cause, a recount is the same one function over the whole series, and recounting twice with the same
  inputs changes nothing. A cause that comes while the series is being counted sets a flag and the series is counted
  once more when the running count ends; series are counted independently of each other.

Neighbours (all over the bus): catalog.series.get / .list, catalog.sources.by_profile, sources.table.get,
rules.apply (one request per source with a profile, all lines of its table at once), voices.groups.resolve (all
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

from core import BaseModule
from core.contracts import (REFERENCE_CHOICE, RUN_CHANGED, VOICE_PLAYER, RunChanged, availability, parse_group_target, CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionPreviewReply,
                            CompositionPreviewRequest, CompositionReply, EpisodeRef, EpisodeSettings,
                            GroupsResolveReply, GroupsResolveRequest, PreviewLine, ReleaseTable, RowState, RulesApplyReply,
                            RuleLine, RulesApplyRequest, RulesProfileChanged, SeriesSource, SettingsReset, SettingsSet,
                            SourceStatus, SourceType, TableGetReply, TableGetRequest, TableState, VoiceOrderSet)
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason

from . import levers, project, settings
from .project import SourceInput
from .repository import CompositionRepository, StoredSource

OWNER = "composition"


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
        self._owed: set[int] = set()
        self._owed_refresh: dict[int, set[int]] = {}
        # The pages of our sources by canonical address, so that an event of `sources` finds them without a query.
        self._pages: dict[str, dict[int, int]] = {}
        self._page_of: dict[int, str] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("composition.get", self.on_get)
        # Waits for a whole count, so the requests for different series must not queue behind each other.
        self.handle("composition.recompute", self.on_recompute, concurrent=True)
        self.handle("composition.preview", self.on_preview, concurrent=True)
        self.handle("composition.settings.set", self.on_settings_set)
        self.handle("composition.settings.reset", self.on_settings_reset)
        self.handle("series.added", self.on_series_added)
        self.handle("series.updated", self.on_series_updated)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("series.source.added", self.on_source_changed)
        self.handle("series.source.updated", self.on_source_changed)
        self.handle("series.source.removed", self.on_source_changed)
        self.handle("sources.table.ready", self.on_table_ready)
        self.handle("sources.table.failed", self.on_table_failed)
        self.handle("rules.profile.changed", self.on_profile_changed)
        self.handle("voices.settings.changed", self.on_groups_changed)
        self.handle(RUN_CHANGED, self.on_run_changed)
        self.handle("composition.voice_order.set", self.on_voice_order_set)

    # --- start ------------------------------------------------------------------------------

    async def on_start(self) -> None:
        try:
            for series_id, source_id, url in await self.repo.pages():
                self._remember(source_id, series_id, url)
            await self._resume()
        except Exception:  # noqa: BLE001 — the other duties of the module do not wait for the recounts
            self.log.exception("пересчёт раскладок при старте не начат")

    async def _resume(self) -> None:
        listing = await self._ask("catalog.series.list", {})
        known = set(await self.repo.series_ids())
        present = {each["id"] for each in listing}
        for each in listing:
            if each["id"] in known or any(source.get("source_type") != SourceType.TORRENT
                                          for source in each.get("sources", [])):
                self._schedule(each["id"])
        for series_id in known - present:
            await self._purge(series_id)

    async def idle(self) -> None:
        """Returns when no series is being counted; the signal of completion for whoever stops or checks the module."""
        while self._flights:
            await asyncio.gather(*(flight.task for flight in list(self._flights.values()) if flight.task),
                                 return_exceptions=True)

    # --- queries and commands ------------------------------------------------------------------

    async def on_get(self, env: Envelope) -> dict:
        request = CompositionGetRequest.model_validate(env.payload)
        stored = await self.repo.stored(request.series_id)
        if request.sources_only:
            return CompositionReply(series_id=request.series_id, rev=stored.rev,
                                    sources=[each.summary for each in stored.sources]).model_dump(mode="json")
        records = settings.canonical(stored.records)
        episodes = project.layout_episodes(stored.rows)
        reply = CompositionReply(
            series_id=request.series_id, rev=stored.rev,
            rows=[row for row in stored.rows if request.include_excluded or not row.excluded],
            sources=[each.summary for each in stored.sources],
            levels=settings.levels(records, {season for season, _ in episodes}),
            settings=[EpisodeSettings(season=season, episode=episode,
                                      levers=settings.effective(records, season, episode))
                      for season, episode in sorted(episodes)],
            levers=levers.specs(), availability=availability.table(),
            # A series counted before the orders were kept shows its default order until its next count writes it.
            voice_orders=project.season_voices(project.season_orders(stored.orders, stored.rows, {}), stored.rows))
        return reply.model_dump(mode="json")

    async def on_voice_order_set(self, env: Envelope) -> dict:
        """The user's order of the voice-overs of a season: the order of the tracks in its containers (ЭТАП_11). It is a
        plan: the containers follow it at the next sync."""
        command = VoiceOrderSet.model_validate(env.payload)
        self._require_editable(command.series_id)
        await self.request("catalog.series.get", {"series_id": command.series_id})
        async with self._lock(command.series_id):
            stored = await self.repo.stored(command.series_id)
            orders = project.season_orders(stored.orders, stored.rows, {})
            shown = next((each for each in project.season_voices(orders, stored.rows) if each.season == command.season), None)
            current = orders.get(command.season, [])
            wanted = project.reorder(current, [voice.label for voice in shown.voices] if shown else [], command.labels)
            if wanted is None:
                raise ValueError(reason("voice_order_mismatch", season=command.season))
            if wanted == current and orders == stored.orders:
                return {"ok": True}
            orders[command.season] = wanted
            rev = stored.rev + 1
            await self.repo.write(command.series_id, orders=orders, rev=rev)
            episodes = sorted(number for season, number in project.layout_episodes(stored.rows) if season == command.season)
            self._publish_changed(command.series_id, rev, {(command.season, number) for number in episodes},
                                  summary_only=False)
        return {"ok": True}

    async def on_preview(self, env: Envelope) -> dict:
        """An address that is no source of a series yet, as the add window and the test of rules show it. It reads
        the table, builds the summary and the lines of the constructor by the same code as for a series, and writes
        nothing: no row, no source, no revision."""
        request = CompositionPreviewRequest.model_validate(env.payload)
        if request.lines is not None:
            return await self._preview_lines(request, request.lines)
        try:
            got = await self._ask("sources.table.get", TableGetRequest(
                url=request.url, options=request.options, refresh=request.refresh).model_dump(mode="json"),
                model=TableGetReply)
            table = got.table if got.table is not None and got.table.complete else None
            summary, lines, rows = None, [], []
            if table is not None:
                source = SeriesSource(id=0, series_id=0, url=request.url, source_type=SourceType.WEB_VIDEO,
                                      parser_profile_id=request.profile_id,
                                      vk_search_mode=request.options.get("vk_search_mode", "search"),
                                      players_off=request.players_off, vibix_season=request.vibix_season,
                                      voice_name=request.voice_name)
                answer = None
                if request.profile_id is not None:
                    answer = await self._answer(source, table, request.series_season)
                item = SourceInput(source, table, answer, got.reason if got.state == TableState.FAILED else None,
                                   got.url)
                names = project.voice_names([item])
                groups = await self._groups(names) if names else {}
                projection = project.project([item], groups, {})
                summary = projection.sources[0]
                rows = [row for row in projection.rows if not row.excluded]
                results = answer.results if answer is not None else {}
                heights = {row.key: list(row.heights) for row in table.rows if row.state == RowState.PLAYABLE}
                lines = [PreviewLine(key=line.key, line=line.text, result=results.get(line.key),
                                     heights=heights.get(line.key, []))
                         for line in project.lines_for(source, table)]
        except _Unavailable as exc:
            raise ValueError(exc.reason) from exc
        return CompositionPreviewReply(url=got.url, state=got.state, reason=got.reason,
                                       names=list(table.names) if table is not None else [], summary=summary,
                                       lines=lines, rows=rows).model_dump(mode="json")

    async def _preview_lines(self, request: CompositionPreviewRequest, texts: list[str]) -> dict:
        """Lines the window holds itself: no page and no table, only the profile's answer to each line."""
        lines = [RuleLine(key=str(place), text=text) for place, text in enumerate(texts)]
        results = {}
        if request.profile_id is not None and lines:
            try:
                answer = await self._ask("rules.apply", RulesApplyRequest(
                    profile_id=request.profile_id, lines=lines, series_season=request.series_season,
                ).model_dump(mode="json"), model=RulesApplyReply)
            except _Unavailable as exc:
                raise ValueError(exc.reason) from exc
            results = answer.results
        return CompositionPreviewReply(
            url=request.url, state=TableState.READY,
            lines=[PreviewLine(key=line.key, line=line.text, result=results.get(line.key)) for line in lines],
        ).model_dump(mode="json")

    async def on_recompute(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        if series_id in self._deleted:
            raise LookupError(f"сериал {series_id} не найден")
        self._schedule(series_id, ordered=True)
        flight = self._flights[series_id]
        answer: asyncio.Future = asyncio.get_running_loop().create_future()
        # The count that is running began before this request; the one it is made to run again is the first to begin after.
        flight.waiters.append((flight.started + 1, answer))
        outcome: _Outcome = await answer
        if series_id in self._deleted:
            raise LookupError(f"сериал {series_id} не найден")
        if outcome.failure is not None:
            raise ValueError(outcome.failure)
        return {"rev": outcome.rev, "changed": outcome.changed}

    async def on_settings_set(self, env: Envelope) -> dict:
        record = SettingsSet.model_validate(env.payload)
        problem = settings.validate(record)
        if problem is not None:
            raise ValueError(problem)
        self._require_editable(record.series_id)
        await self._require_pickable(record)
        return await self._edit(record.series_id, lambda records: settings.apply_set(records, record))

    def _require_editable(self, series_id: int) -> None:
        """A lever of a series whose sync is under way is not changed: the sync works by what it found (the table of
        what is allowed, as every owner of the series refuses)."""
        availability.require(availability.Action.EDIT,
                             availability.SERIES_SCANNING if series_id in self._frozen else availability.SERIES_IDLE)

    async def _require_pickable(self, record: SettingsSet) -> None:
        """A variant is picked as the reference, or as the first one of its group, only where the table of what is allowed
        lets it: the same rule the window draws its marks by."""
        if not isinstance(record.value, dict) or record.lever not in (REFERENCE_CHOICE, VOICE_PLAYER):
            return
        group = record.value.get("group") if record.lever == REFERENCE_CHOICE else parse_group_target(record.target or "")
        action = availability.Action.CHOOSE_REFERENCE if record.lever == REFERENCE_CHOICE else availability.Action.PREFER_VARIANT
        stored = await self.repo.stored(record.series_id)
        # The state is that of the level the setting is for, as the window shows it: the lines of that season or episode.
        found = [row for row in availability.lines_of_level(stored.rows, record.season, record.episode)
                 if row.voice_label == group and row.source_id == record.value.get("source_id")
                 and row.player == record.value.get("player")]
        availability.require(action, availability.variant_state(found))

    async def on_settings_reset(self, env: Envelope) -> dict:
        scope = SettingsReset.model_validate(env.payload)
        problem = settings.validate_reset(scope)
        if problem is not None:
            raise ValueError(problem)
        self._require_editable(scope.series_id)
        return await self._edit(scope.series_id, lambda records: settings.apply_reset(records, scope))

    async def _edit(self, series_id: int, change) -> dict:
        # A command about a series the catalog does not have is refused with the catalog's own answer.
        await self.request("catalog.series.get", {"series_id": series_id})
        async with self._lock(series_id):
            if series_id in self._deleted:
                raise LookupError(f"сериал {series_id} не найден")
            stored = await self.repo.stored(series_id)
            before = settings.canonical(stored.records)
            after = change(before)
            if after == before:
                return {"ok": True}
            episodes = project.layout_episodes(stored.rows)
            touched = settings.affected(before, after, episodes)
            # A header that no episode follows still changes on the screen: that is told to windows, not to consumers.
            seasons = {season for season, _ in episodes}
            shown = bool(touched) or settings.levels(before, seasons) != settings.levels(after, seasons)
            rev = stored.rev + 1 if shown else None
            await self.repo.write(series_id, records=after, rev=rev)
            if shown:
                self._publish_changed(series_id, rev, touched, summary_only=not touched)
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
        self.publish_event("series.purged", {"series_id": series_id, "owner": OWNER})

    def on_source_changed(self, env: Envelope) -> None:
        refresh = () if env.topic.endswith(".removed") else (env.payload["source_id"],)
        self._schedule(env.payload["series_id"], refresh)

    def on_table_ready(self, env: Envelope) -> None:
        self._wake(env.payload["url"])

    def on_table_failed(self, env: Envelope) -> None:
        # The verdict is in `sources` before the event goes out; the recount reads it there and asks for no new read.
        self._wake(env.payload["url"])

    async def on_profile_changed(self, env: Envelope) -> None:
        profile_id = RulesProfileChanged.model_validate(env.payload).profile_id
        try:
            sources = await self._ask("catalog.sources.by_profile", {"profile_id": profile_id})
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
        flight.task = asyncio.create_task(self._run(series_id, flight), name=f"composition:{series_id}")
        self._bg.add(flight.task)
        flight.task.add_done_callback(self._bg.discard)

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
                    answer.set_exception(LookupError(f"сериал {series_id} не найден"))
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
            self.publish_event("composition.failed",
                               CompositionFailed(series_id=series_id, reason=failure).model_dump(mode="json"))
            return _Outcome(failure=failure)

    async def _gather(self, series_id: int, flight: _Flight, refresh: set[int]
                      ) -> tuple[list[SourceInput], dict[str, str | None]]:
        """What the neighbours say about the series; nothing is written here."""
        card = await self._ask("catalog.series.get", {"series_id": series_id})
        sources = [SeriesSource.model_validate(each) for each in card.get("sources", [])
                   if each.get("source_type") != SourceType.TORRENT]
        inputs = []
        for source in sources:
            table, failure, url = await self._table(series_id, flight, source, source.id in refresh)
            answer = None
            if table is not None and source.parser_profile_id is not None:
                answer = await self._answer(source, table, card.get("season"))
            inputs.append(SourceInput(source, table, answer, failure, url))
        names = project.voice_names(inputs)
        return inputs, await self._groups(names) if names else {}

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
        request = TableGetRequest(url=source.url, options=source.read_options(), refresh=begin)
        return await self._ask("sources.table.get", request.model_dump(mode="json"), model=TableGetReply)

    async def _answer(self, source: SeriesSource, table: ReleaseTable, season) -> RulesApplyReply:
        lines = project.lines_for(source, table)
        if not lines:
            return RulesApplyReply(results={})
        request = RulesApplyRequest(profile_id=source.parser_profile_id, lines=lines, series_season=season)
        return await self._ask("rules.apply", request.model_dump(mode="json"), model=RulesApplyReply)

    async def _groups(self, names: list[str]) -> dict[str, str | None]:
        reply = await self._ask("voices.groups.resolve", GroupsResolveRequest(names=names).model_dump(),
                                model=GroupsResolveReply)
        return reply.groups

    async def _settle(self, series_id: int, inputs: list[SourceInput], groups: dict[str, str | None]) -> _Outcome:
        """Projects, compares with what is stored, writes in one transaction and tells what changed."""
        async with self._lock(series_id):
            if series_id in self._deleted:
                return _Outcome()
            stored = await self.repo.stored(series_id)
            if not inputs and not stored.sources and not stored.rows and not stored.records:
                return _Outcome(rev=stored.rev)
            result = project.project(inputs, groups, stored.numbering)
            gone = [each.source_id for each in stored.sources if each.source_id not in {i.source.id for i in inputs}]
            before = settings.canonical(stored.records)
            renames = project.group_renames(stored.rows, result.rows)
            orders = project.season_orders(stored.orders, result.rows, renames)
            # What was set for a voice-over group follows the voice-over when the user names it, renames it or dissolves its group.
            records = settings.retarget(
                before, renames,
                lambda group: self.log.info("сериал %d: у слитых групп расходящиеся настройки %s — выбор возвращён уровню выше",
                                            series_id, group[0].lever))
            for source_id in gone:
                records = settings.without_source(records, source_id)
            layout = project.layout_episodes(result.rows)
            if any(summary.status == SourceStatus.READY for summary in result.sources):
                # Without an answered source the layout says nothing of which episodes exist, so nothing is taken away.
                records = settings.within_episodes(records, layout)
            records_changed = records != before
            moved = settings.affected(before, records, layout) if records_changed else set()
            seasons = {season for season, _ in layout}
            headers_changed = records_changed and settings.levels(before, seasons) != settings.levels(records, seasons)

            sources = [StoredSource(item.source.id, item.source.position, self._page_of.get(item.source.id), summary)
                       for item, summary in zip(sorted(inputs, key=lambda i: (i.source.position, i.source.id)),
                                                result.sources)]
            for source_id in gone:
                self._unmap(source_id)
            rows_changed = result.rows != stored.rows
            # The time of a reading is stored with the summary but is no change of it: a page read again is no news.
            shown = [summary.model_copy(update={"read_at": None}) for summary in result.sources]
            summaries_changed = shown != [each.summary.model_copy(update={"read_at": None}) for each in stored.sources]
            layout_changed = rows_changed or bool(moved)
            publish = layout_changed or summaries_changed or headers_changed
            rev = stored.rev + 1 if publish else None
            parts = dict(
                rows=result.rows if rows_changed else None,
                sources=sources if sources != stored.sources else None,
                numbering=result.unknown_voices if result.unknown_voices != stored.numbering else None,
                records=records if records_changed else None, rev=rev,
                orders=orders if orders != stored.orders else None)
            if any(value is not None for value in parts.values()):
                await self.repo.write(series_id, **parts)
            if not publish:
                return _Outcome(rev=stored.rev)
            if layout_changed:
                touched = project.touched_episodes(stored.rows, result.rows) if rows_changed else set()
                whole = touched is None or not (touched | moved)
                self._publish_changed(series_id, rev, set() if whole else touched | moved)
            else:
                self._publish_changed(series_id, rev, set(), summary_only=True)
            return _Outcome(rev=rev, changed=True)

    def _publish_changed(self, series_id: int, rev: int, episodes: set[tuple[int, int]], *,
                         summary_only: bool = False) -> None:
        changed = CompositionChanged(series_id=series_id, rev=rev, summary_only=summary_only, episodes=[
            EpisodeRef(season=season, episode=episode) for season, episode in sorted(episodes)])
        self.publish_event("composition.changed", changed.model_dump(mode="json"))

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
        url = self._page_of.pop(source_id, None)
        if url is not None:
            self._pages.get(url, {}).pop(source_id, None)

    async def _ask(self, topic: str, payload, *, model=None):
        try:
            answer = await self.request(topic, payload)
            return model.model_validate(answer) if model is not None else answer
        except Exception as exc:  # noqa: BLE001 — any failure of a neighbour is one signal: the recount did not happen
            self.log.error("запрос %s не выполнен: %s: %s", topic, type(exc).__name__, exc)
            raise _Unavailable(topic) from exc
