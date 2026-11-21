"""Module conform: bridge to the alignment daemon (track-muxer), mission voice-alignment, stages 5 and 7.

Mirrors the daemon's `conform` registry from its event stream (snapshot, changed, progress) and
pushes it to the interface, queues episode jobs for the voices module and reports how a job ended.
The daemon owns the state; this module keeps no database. The daemon address is the setting `conform.url`
(`ST_CONFORM_URL` while none is stored; without an address the module is idle) and a saved address is taken at once,
without a restart. The work folder is two settings, `conform.work_dir` (as this server names it) and
`conform.work_dir_remote` (the same folder as the daemon names it); no default, the owner names both. Everything the
daemon is given or writes lies inside it, and the paths cross the boundary through these two names. The daemon may run
on Windows: its folder is written as `Z:\\...` or `\\\\server\\share\\...`, and a path of the daemon is read by its own syntax
(calc/conform/paths.py), so it is the same location whatever separators and letter case the daemon writes it with.

What the settings, the jobs and the requests come to is counted in calc/conform; this module holds the connection to the daemon (the
event feed, the HTTP client, its backoff), the mirror of its history and the address in force. Every reply and event is a model of
core/contracts (alignment.py, conform_settings.py), handed over as it is; an event goes on the bus as its JSON.

The daemon is not told down before it was looked at: a request that needs it (submit, reverse, cancel, plot, job.find, job.get of a
job the mirror does not hold, job.keys, params.set) waits for the end of the first attempt to connect to the daemon of the address
in force (the address is read at the start, the feed connects after it) and is answered by its outcome; its own fuse is the one
of its topic. A daemon that was up and dropped is down at once, as before.

Query:
  conform.submit JobSubmit {ref, dubs: [path], out_dir, cache_dir, label?, key?, ref_atrack?, dub_atracks?} -> SubmitReply
      (core/contracts): {outcome, job_id?, reason?, retry}
      One episode: a reference video and the voice-over files to align to it, local paths. `key`
      is written into the job's label so that the job can be found again when the answer is lost.
      The outcome is what was proved. `accepted`: the daemon named the job. `rejected` is final for this request: a path
      outside the shared storage, found before anything is sent, or a refusal of the daemon (it judged no voice-over:
      the voices module offers them again at the next sync). `unavailable`:
      the request certainly did not take effect: with retry=false the daemon is not configured, has no path map or is down
      and the caller waits for conform.daemon.up; with retry=true the connection failed before the request was
      sent (or the daemon asked to come back later) and asking again soon may work. `uncertain`: the request
      was sent and nothing well-formed came back, so the daemon may have the job; the caller keeps the key
      and asks conform.job.find, it does not send again.
  conform.settings.get {} -> ConformSettingsView {url, work_dir, work_dir_remote, clear_finished, params, domain}: what
      applies now.
      `clear_finished` (the setting conform.clear_finished, "true" or "false", written by the pipeline flag
      `/api/settings/conform_clear_finished` of the debug tab): a job that ended and whose result was taken leaves the
      daemon's history on its own, unless it ended in an error; off, the history keeps it until the owner deletes it.
  conform.settings.set ConformSettingsDraft {url, work_dir, work_dir_remote} -> ConformSettingsSaved {saved, problems, settings}:
      checked as a whole, written through settings.values.set_many and taken at once, or refused with coded problems and nothing
      written.
  conform.params.set ConformParams {audio_method, drift_speed_pct, keep_tmp, fill_silence, extend_edges} ->
      ConformSettingsSaved: how the daemon is asked to align (settings conform.audio_method, conform.drift_speed_pct,
      conform.keep_tmp, conform.fill_silence, conform.extend_edges; never stored: MuQ, the daemon's drift, no
      checkpoints, the cuts filled, the edges extended), checked against what the daemon says it accepts
      (`GET /conform/settings`: the view's `domain`; unknown while the daemon has not said: refused) and written whole;
      every job sent after it carries it (an episode: all but `extend_edges`; a compilation: all but `fill_silence`).
  conform.reverse ReverseSubmit {compilation, compilation_atrack?, episodes: [{path, atrack}], out_dir, label?} ->
      ReverseReply {outcome, jobs, reason?, retry} (stages 12 and 14): the picture of one video (a compilation, or a
      single episode higher than the markup reference) laid onto the timeline of the single video of each episode, one
      job of kind `reverse` each (what the picture lacks is taken from that video); `compilation_atrack` is the audio
      track of the picture that is its own sound. The folder of each episode
      is the label of its job and carries the key that finds it again (`… #key`); `jobs` maps the keys to the jobs the
      service named. The outcome reads as for conform.submit; a key the answer does not name is asked by its key.
  conform.workspace {} -> ConformFolders {downloads, conform}: the two folders inside the work folder, where voice-overs wait for
      alignment and where the service works; both empty while no work folder is named.
  conform.job.folders JobFoldersOf {dub_folder} -> JobFolders {out_dir, cache_dir}: the folders the daemon is asked to write the aligned
      tracks and its cache to for an episode folder of the service. Pure: no daemon is asked.
  conform.cancel JobRef {job_id} -> JobCancelled {done: bool} — stop a job that waits or runs (its record stays as `cancelled`), or
      delete the record of a job that ended: the daemon then drops the files it keeps for the record (graphs, journal, time map,
      cache) and leaves the output audio. `done` is true when it is stopped or the daemon does not know it,
      false when the daemon could not be reached. An answer of failure is checked against what the daemon holds after
      it: a record that is gone, or is `cancelled`, is done.
  conform.job.keys {} -> JobKeys {reachable, keys}: the keys of the jobs the daemon's history holds (the end of their labels).
  conform.plot PlotRef {job_id, name} -> PlotReply {found: bool, data?: base64 PNG} — the picture of a plot named in a result (`plot`);
      only a plain PNG name is asked of the daemon.
  conform.job.find JobKey {key} -> JobFound {reachable: bool, job_id: str | None} — the job sent with this key.
  conform.job.get JobRef {job_id} -> AlignmentJob {job_id, status, error, results}   status `gone` only when the daemon was
      asked and does not know the job; when it cannot be asked the query fails; results as in
      conform.job.finished.
Event:
  conform.queue.changed ConformQueue {daemon, tasks}   the whole picture on every change and whenever an
                                          interface connects (gateway.sse.clients)
  conform.job.finished AlignmentJob {job_id, kind, status, error, results: [{dub, ok, refused, skipped, out_path, suspect,
      critical, warnings, resid_ms, error, from_ref_s, from_dub_s}]}   once per job that ended (done, failed, cancelled);
      out_path is a local path. A pair that is not `ok` is `refused` only when the service itself turned it down (its trace
      holds a decision that went `abort`); otherwise it crashed in the service and no judgement of the voice-over was made.
      The results of a failed or stopped job are those of the pairs it had finished.
  conform.daemon.up {}                    the daemon became reachable: waiting work may be sent
  conform.job.removed JobRemoved {job_id}  the daemon dropped the record of a job (deleted by the owner, by the service
                                          or by the cleanup of this project)
  conform.settings.changed ConformSettingsView   after every save, so that every open interface shows it
A task is the compact view of one daemon job (ConformTask): id, label (the human part), key (the one the label carries,
if any), series_id (the series the key says the job works for: contracts/alignment.py), status, progress, stage, stage_pct,
detail, dub_index, dub_total, cur_dub, dubs (file names), elapsed_s, error and results[{dub, plot, ok, refused, skipped, resid_ms,
coverage, cuts, suspect, warnings, critical, error}]; `plot` names the picture of the voice-over's plot, if the daemon made one.
"""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Callable
from typing import Any

import httpx

from calc.conform import jobs as conform_jobs
from calc.conform import settings as conform_settings
from calc.conform.jobs import (episode_body, finished_view, job_with_key, keys_of, named_jobs, picture_of, plot_is_plain, reverse_body,
                               reverse_keys, reverse_label, stopped_or_gone, unannounced)
from calc.conform.paths import reverse, roots, workspace
from calc.conform.settings import PREFIX, REMOTE_DIR_KEY, URL_KEY, WORK_DIR_KEY, mapping_of
from core import storage
from core.contracts import (AlignmentJob, ConformFolders, ConformParams, ConformQueue, ConformSettingsDraft, ConformSettingsSaved,
                            ConformSettingsView, DaemonState, DaemonUp, JobCancelled, JobFolders, JobFoldersOf, JobFound, JobKeys, JobKey,
                            JobRef, JobRemoved, JobStatus, JobSubmit, PlotRef, PlotReply, ReverseReply, ReverseSubmit, SubmitOutcome,
                            SubmitReply)
from core.envelope import Envelope
from core.module import BaseModule, BusRequestError
from core.reasons import describe, reason
from core.topics import (CONFORM_CANCEL, CONFORM_DAEMON_UP, CONFORM_JOB_FIND, CONFORM_JOB_FINISHED, CONFORM_JOB_FOLDERS, CONFORM_JOB_GET,
                         CONFORM_JOB_KEYS, CONFORM_JOB_REMOVED, CONFORM_PARAMS_SET, CONFORM_PLOT, CONFORM_QUEUE_CHANGED, CONFORM_REVERSE,
                         CONFORM_SETTINGS_CHANGED, CONFORM_SETTINGS_GET, CONFORM_SETTINGS_SET, CONFORM_SUBMIT, CONFORM_WORKSPACE,
                         GATEWAY_SSE_CLIENTS, SETTINGS_VALUE_GET, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY)

from .client import DaemonClient, DaemonRefused, NotDelivered, Unconfirmed

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0


class ConformModule(BaseModule):
    name = "conform"

    def __init__(self, bus, client: DaemonClient | None = None, backoff_start: float = BACKOFF_START_S, *,
                 default_url: str = "", client_factory: Callable[[str], DaemonClient] = DaemonClient) -> None:
        # A ready client is the address in force from the start; without one the address is read at the start.
        self._client = client
        self._client_factory = client_factory
        self._default_url = default_url
        self._address_lock = asyncio.Lock()
        self._backoff_start = backoff_start
        # The mirror of the history of the daemon: its jobs as it sends them, by number.
        self._jobs: dict[str, dict[str, Any]] = {}
        self._announced: set[str] = set()
        self._daemon = DaemonState(up=False, error=None)
        # Set when the first look at the daemon of the address in force is over: it answered, or did not, or there is none to ask.
        self._looked = asyncio.Event()
        self._runner: asyncio.Task | None = None
        super().__init__(bus)

    def register(self) -> None:
        self.handle(CONFORM_SUBMIT, self.on_submit)
        self.handle(CONFORM_REVERSE, self.on_reverse)
        self.handle(CONFORM_JOB_GET, self.on_job_get)
        self.handle(CONFORM_WORKSPACE, self.on_workspace)
        self.handle(CONFORM_JOB_FOLDERS, self.on_job_folders)
        self.handle(CONFORM_CANCEL, self.on_cancel)
        self.handle(CONFORM_PLOT, self.on_plot)
        self.handle(CONFORM_SETTINGS_GET, self.on_settings_get)
        self.handle(CONFORM_SETTINGS_SET, self.on_settings_set)
        self.handle(CONFORM_PARAMS_SET, self.on_params_set)
        self.handle(CONFORM_JOB_FIND, self.on_job_find)
        self.handle(CONFORM_JOB_KEYS, self.on_job_keys)
        self.handle(GATEWAY_SSE_CLIENTS, self.on_clients)

    async def _known(self) -> None:
        """The daemon is looked at before anything is said of it: the address is read at the start and the feed makes its first
        attempt to connect after that, so until the attempt ends the daemon is not down, it is not known. A request that comes
        meanwhile waits for the end of the attempt (its fuse is that of its topic) and is answered by what it found."""
        if self._looked.is_set():
            return
        self.log.info("conform: запрос ждёт окончания первой попытки подключения к демону")
        while not self._looked.is_set():  # another address starts the look over
            await self._looked.wait()

    def _reachable(self) -> bool:
        return self._client is not None and self._daemon.up

    def snapshot(self) -> ConformQueue:
        return picture_of(self._jobs, self._daemon)

    async def _stored(self, key: str) -> str | None:
        try:
            reply = await self.reply_of(SETTINGS_VALUE_GET, {"key": key})
        except (BusRequestError, asyncio.TimeoutError):
            return None
        return reply.value

    async def _values(self) -> dict[str, str | None]:
        """Every setting of the module in one read."""
        try:
            reply = await self.reply_of(SETTINGS_VALUES_BY_PREFIX, {"prefix": PREFIX})
        except (BusRequestError, asyncio.TimeoutError):
            return {}
        return reply.values

    async def _path_map(self, values: dict[str, str | None] | None = None) -> dict[str, str]:
        values = await self._values() if values is None else values
        return mapping_of(values.get(WORK_DIR_KEY), values.get(REMOTE_DIR_KEY))

    async def on_settings_get(self, env: Envelope) -> ConformSettingsView:
        return await self._settings_view()

    async def on_settings_set(self, env: Envelope) -> ConformSettingsSaved:
        draft = conform_settings.clean(ConformSettingsDraft.model_validate(env.payload))
        # The disk is asked here, off the loop: whether a file system is mounted on the folder is a fact the check is given.
        mounted = bool(draft.work_dir) and await storage.call(os.path.ismount, draft.work_dir)
        problems = conform_settings.check(draft, mounted)
        if not problems:
            await self.request(SETTINGS_VALUES_SET_MANY, {"values": {
                **conform_settings.stored(draft), **conform_settings.next_revision(await self._values())}})
            await self._use_address(draft.url)
        view = await self._settings_view()
        if not problems:
            self.log.info("conform: настройки сервера сохранены, адрес %s", draft.url or "не задан")
            self.publish_event(CONFORM_SETTINGS_CHANGED, view.model_dump(mode="json"))
        return ConformSettingsSaved(saved=not problems, problems=problems, settings=view)

    async def on_params_set(self, env: Envelope) -> ConformSettingsSaved:
        """How the daemon is asked to align, checked against what it says it accepts and written whole, or refused with
        coded problems and nothing written; the next job takes it."""
        params = ConformParams.model_validate(env.payload)
        await self._known()
        problems = conform_settings.check_params(params, self._domain())
        if not problems:
            await self.request(SETTINGS_VALUES_SET_MANY, {"values": {
                **conform_settings.stored_params(params), **conform_settings.next_revision(await self._values())}})
        view = await self._settings_view()
        if not problems:
            self.log.info("conform: параметры выравнивания сохранены: %s", params.model_dump(mode="json"))
            self.publish_event(CONFORM_SETTINGS_CHANGED, view.model_dump(mode="json"))
        return ConformSettingsSaved(saved=not problems, problems=problems, settings=view)

    def _domain(self):
        return conform_settings.domain_of(self._daemon.params)

    async def _settings_view(self) -> ConformSettingsView:
        url = self._client.base_url if self._client is not None else ""
        return conform_settings.view(url, await self._values(), self._domain())

    async def _use_address(self, url: str) -> None:
        """Point the bridge at another daemon, or at none: the feed of the old one ends, the picture starts over."""
        async with self._address_lock:
            if url == (self._client.base_url if self._client is not None else ""):
                return
            await self._stop_feed()
            self._client = self._client_factory(url) if url else None
            self._jobs.clear()
            self._daemon = DaemonState(up=False, error=None)
            self._looked.set()  # whoever waited for the old address looks again, at the new one
            self._looked = asyncio.Event()
            if self._client is None:
                self._looked.set()
            self._publish()
            self._start_feed()

    def _unavailable(self) -> str | None:
        """The reason nothing can be sent to the daemon now: there is none to send to, or it is down; none when it is up."""
        if self._client is None:
            return reason("conform_not_configured")
        if not self._daemon.up:
            return reason("conform_down", error=self._daemon.error or "")
        return None

    async def on_submit(self, env: Envelope) -> SubmitReply:
        request = JobSubmit.model_validate(env.payload)
        await self._known()
        if (why := self._unavailable()) is not None:
            return SubmitReply.unavailable(why, retry=False)
        label = request.label or ""
        values = await self._values()
        mapping = await self._path_map(values)
        if not mapping:
            return SubmitReply.unavailable(reason("conform_no_work_dir"), retry=False)
        try:  # before anything is sent: a problem here is a certain refusal
            body = episode_body(request, mapping, conform_settings.params_of(values))
        except ValueError as exc:
            self.log.warning("conform: задача %s отклонена: %s", label, describe(str(exc)))
            return SubmitReply.rejected(str(exc))
        try:
            job = await self._client.enqueue(body)
        except DaemonRefused as exc:
            self.log.warning("conform: задача %s отклонена: %s", label, describe(str(exc)))
            return SubmitReply.rejected(str(exc))
        except NotDelivered as exc:
            self.log.warning("conform: задача %s не поставлена: %s", label, exc)
            return SubmitReply.unavailable(reason("conform_unreachable", error=str(exc)), retry=True)
        except Unconfirmed as exc:
            # The request may have reached the daemon: the caller must ask by the key, not send again.
            self.log.warning("conform: ответа на постановку %s нет: %s", label, exc)
            return SubmitReply.uncertain(reason("conform_no_answer", error=str(exc)))
        self.log.info("conform: поставлена задача %s (%s), озвучек %d", job["id"], label, len(body["dubs"]))
        return SubmitReply.accepted(str(job["id"]))

    async def on_reverse(self, env: Envelope) -> ReverseReply:
        """A batch of cuts out of one compilation (stage 12): one job per episode, each labelled by the folder of its
        episode, which carries the key that finds the job again. The outcome is told as for `conform.submit`."""
        request = ReverseSubmit.model_validate(env.payload)
        label = reverse_label(request)
        await self._known()
        if (why := self._unavailable()) is not None:
            return ReverseReply(outcome=SubmitOutcome.UNAVAILABLE, reason=why)
        values = await self._values()
        mapping = await self._path_map(values)
        if not mapping:
            return ReverseReply(outcome=SubmitOutcome.UNAVAILABLE, reason=reason("conform_no_work_dir"))
        keys = reverse_keys(request)
        try:  # before anything is sent: a problem here is a certain refusal
            body = reverse_body(request, label, keys, mapping, conform_settings.params_of(values))
        except ValueError as exc:
            self.log.warning("conform: разбор %s отклонён: %s", label, describe(str(exc)))
            return ReverseReply(outcome=SubmitOutcome.REJECTED, reason=str(exc))
        try:
            jobs = await self._client.reverse(body)
        except DaemonRefused as exc:
            self.log.warning("conform: разбор %s отклонён: %s", label, describe(str(exc)))
            return ReverseReply(outcome=SubmitOutcome.REJECTED, reason=str(exc))
        except NotDelivered as exc:
            self.log.warning("conform: разбор %s не поставлен: %s", label, exc)
            return ReverseReply(outcome=SubmitOutcome.UNAVAILABLE, reason=reason("conform_unreachable", error=str(exc)), retry=True)
        except Unconfirmed as exc:
            self.log.warning("conform: ответа на разбор %s нет: %s", label, exc)
            return ReverseReply(outcome=SubmitOutcome.UNCERTAIN, reason=reason("conform_no_answer", error=str(exc)))
        named = named_jobs(jobs, keys)
        self.log.info("conform: поставлен разбор %s — задач %d из %d", label, len(named), len(keys))
        return ReverseReply(outcome=SubmitOutcome.ACCEPTED, jobs=named)

    async def on_workspace(self, env: Envelope) -> ConformFolders:
        return roots(await self._stored(WORK_DIR_KEY))

    async def on_job_folders(self, env: Envelope) -> JobFolders:
        return workspace(JobFoldersOf.model_validate(env.payload).dub_folder)

    async def on_cancel(self, env: Envelope) -> JobCancelled:
        job_id = JobRef.model_validate(env.payload).job_id
        await self._known()
        if not self._reachable():
            self.log.info("conform: задачу %s отменить некому — демон недоступен", job_id)
            return JobCancelled(done=False)
        try:
            known = await self._client.cancel(job_id)
        except httpx.HTTPError as exc:
            # The answer is not the outcome: the daemon may drop the record and fail after it (10.11: cf00079 gone, 500).
            # What it holds now says whether the job was stopped or deleted.
            try:
                left = await self._client.job(job_id)
            except httpx.HTTPError:
                left = {"status": "unknown"}
            done = stopped_or_gone(left)
            self.log.warning("conform: на отмену задачи %s демон ответил ошибкой (%s); задача %s", job_id, exc,
                             "удалена" if left is None else "отменена" if done else f"осталась в состоянии {left.get('status')}")
            return JobCancelled(done=done)
        self.log.info("conform: задача %s %s", job_id, "отменена" if known else "демону неизвестна")
        return JobCancelled(done=True)

    async def on_plot(self, env: Envelope) -> PlotReply:
        """The picture of a plot of a job, taken from the daemon; the names are checked before anything is asked."""
        ref = PlotRef.model_validate(env.payload)
        if not plot_is_plain(ref):
            return PlotReply(found=False)
        await self._known()
        if not self._reachable():
            raise RuntimeError(reason("conform_down", error=self._daemon.error or ""))
        try:
            picture = await self._client.plot(ref.job_id, ref.name)
        except httpx.HTTPError as exc:
            raise RuntimeError(reason("conform_down", error=str(exc))) from exc
        if picture is None:
            return PlotReply(found=False)
        return PlotReply(found=True, data=base64.b64encode(picture).decode("ascii"))

    async def _history(self) -> list[dict[str, Any]] | None:
        """Every job the daemon's history holds; none when it cannot be asked."""
        await self._known()
        if not self._reachable():
            return None
        try:
            return await self._client.jobs()
        except httpx.HTTPError:
            return None

    async def on_job_find(self, env: Envelope) -> JobFound:
        key = JobKey.model_validate(env.payload).key
        history = await self._history()
        if history is None:
            return JobFound(reachable=False, job_id=None)
        return JobFound(reachable=True, job_id=job_with_key(history, key))

    async def on_job_keys(self, env: Envelope) -> JobKeys:
        """The keys of the jobs the daemon's history holds (`… #key` of their labels): what a caller keeps a folder of a
        job for."""
        history = await self._history()
        if history is None:
            return JobKeys(reachable=False, keys=())
        return JobKeys(reachable=True, keys=keys_of(history))

    async def on_job_get(self, env: Envelope) -> AlignmentJob:
        job_id = JobRef.model_validate(env.payload).job_id
        job = self._jobs.get(job_id)
        if job is None:
            await self._known()
            if not self._reachable():
                raise RuntimeError(reason("conform_down", error=self._daemon.error or ""))  # not asked is not "gone"
            job = await self._client.job(job_id)
        if job is None:
            return AlignmentJob(job_id=job_id, status=JobStatus.GONE)
        return finished_view(job, reverse(await self._path_map()))

    async def on_clients(self, env: Envelope) -> None:
        # A new interface has no history: it gets the whole picture at once.
        if env.payload.get("count"):
            self._publish()

    async def on_start(self) -> None:
        if self._client is None:
            stored = await self._stored(URL_KEY)
            url = self._default_url if stored is None else stored
            self._client = self._client_factory(url) if url else None
        self._start_feed()
        if self._client is None:
            self._looked.set()  # no address: there is no daemon to look at

    async def on_stop(self) -> None:
        self._looked.set()  # nobody waits for a look that will not be made
        await self._stop_feed()

    def _start_feed(self) -> None:
        if self._client is not None:
            self._runner = self.spawn(self._run(), name="conform:feed", lasting=True)

    async def _stop_feed(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        if self._client is not None:
            await self._client.close()

    def _publish(self) -> None:
        # A report names what it knows: a daemon that is down has no version, and the picture says so by leaving it out.
        self.publish_event(CONFORM_QUEUE_CHANGED, self.snapshot().model_dump(mode="json", exclude_unset=True))

    async def _run(self) -> None:
        assert self._client is not None
        delay = self._backoff_start
        while True:
            try:
                self._daemon = DaemonState(up=True, error=None, **await self._client.info())
                self._looked.set()
                self._publish()
                self.publish_event(CONFORM_DAEMON_UP, DaemonUp().model_dump(mode="json"))
                # What the daemon accepts is part of the settings screen: an open one learns it now.
                self.publish_event(CONFORM_SETTINGS_CHANGED, (await self._settings_view()).model_dump(mode="json"))
                self.log.info("conform: подключён к демону %s", self._client.base_url)
                async for name, payload in self._client.events():
                    delay = self._backoff_start
                    self._apply(name, payload)
                    self._publish()
                    await self._announce()
                problem = reason("stream_closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport failure means "daemon unreachable"
                problem = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self._daemon = DaemonState(up=False, error=problem)
            self._looked.set()
            self._jobs.clear()
            self._publish()
            self.log.warning("conform: демон недоступен (%s), повтор через %.0f с", describe(problem), delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

    async def _announce(self) -> None:
        """Report every job that ended since the last look, once."""
        ended = unannounced(self._jobs, self._announced)
        if not ended:
            return
        to_local = reverse(await self._path_map())
        for job in ended:
            self._announced.add(job["id"])
            self.publish_event(CONFORM_JOB_FINISHED, finished_view(job, to_local).model_dump(mode="json"))

    def _apply(self, name: str, payload: dict[str, Any]) -> None:
        self._jobs, removed = conform_jobs.mirrored(self._jobs, name, payload)
        for job_id in removed:
            self._announced.discard(job_id)
            self.publish_event(CONFORM_JOB_REMOVED, JobRemoved(job_id=job_id).model_dump(mode="json"))
