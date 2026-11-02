"""The cleanup journal carried out: what is removed, when, and what ends an entry.

A thing that has to go (a file, an artifact of the output, a tree, a job of the service, a submission whose answer is not
known) is written to the journal first and removed after; only the end of the removal, or the loss of the reason to remove,
ends the entry, and what does not end stays on the journal for the recovery. The name is owned before anything about it is
looked at, so a voice-over that takes the name meanwhile waits for the end of the removal. A job is cancelled in the
service, and only a confirmed stop ends its entry and lets the folder it wrote in be cleaned.
"""
from __future__ import annotations

import asyncio
import errno
import os
import shutil
from typing import Awaitable, Callable

from core import BusRequestError, storage
from core.contracts import DubStatus as S
from core.storage import REMOVE_TIMEOUT_S

from .repository import HANDED_OVER, VoicesRepository

# What the cleanup journal can hold: a plain file, a tree, an artifact of the output, a job of the service and
# a submission whose answer is not known (the key finds the job).
PATH, TREE, ARTIFACT, JOB, SUBMISSION = "path", "tree", "artifact", "job", "submission"


def remove_if_empty(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def prune(out_dir: str) -> None:
    """What the removal of a job's folder leaves empty goes too: the job's own folders of the same key (its cache),
    then the folders around them. Another job's empty folder is not touched, the key tells them apart."""
    key = os.path.basename(out_dir)
    episode = os.path.dirname(os.path.dirname(out_dir))
    if os.path.isdir(episode):
        for entry in list(os.scandir(episode)):
            if entry.is_dir(follow_symlinks=False):
                remove_if_empty(os.path.join(entry.path, key))
                remove_if_empty(entry.path)
    for folder in (episode, os.path.dirname(episode)):
        remove_if_empty(folder)


class Cleanup:
    def __init__(self, repo: VoicesRepository, output, log, *, io: Callable[..., Awaitable],
                 request: Callable[..., Awaitable], retry: Callable[[str], None], sending: set[str],
                 poke: Callable[[str], None]) -> None:
        self._repo, self._output, self._log = repo, output, log
        self._io = io            # a call into the storage that waits for a limited time
        self._request = request  # a question to a neighbour (the service of alignment)
        self._retry = retry      # asks the recovery to carry the entry again
        self._sending = sending  # keys of submissions whose request is on its way
        self._poke = poke        # tells the recovery that what waited for an entry may go on

    def unlink(self, path: str, kind: str) -> None:
        """Remove what the kind says and confirm it: success is the name being free, not the absence of an error."""
        if kind == TREE:
            shutil.rmtree(path)
        elif kind == ARTIFACT:
            self._output.remove(path)
        else:
            os.remove(path)
        if self._output.exists(path) if kind == ARTIFACT else os.path.lexists(path):
            raise OSError(errno.EEXIST, "the name is still taken after its removal", path)

    async def discard(self, path: str, *, kind: str = PATH) -> bool:
        """Remove a file, an artifact or a tree: written to the journal first, then removed. True means it is
        gone; anything else stays on the journal and is carried to the end by the recovery."""
        await self._repo.journal_cleanup(path, kind, "removal begun")
        if await self.clear(path):
            return True
        self._retry(path)
        return False

    @staticmethod
    def held(path: str, rows: list[dict]) -> bool:
        """True when a voice-over holds the path or something inside it."""
        inside = path.rstrip(os.sep) + os.sep
        return any(held == path or held.startswith(inside)
                   for row in rows
                   for held in (row["output_path"], row["move_from"], row["aligned_path"], row["temp_path"])
                   if held)

    @staticmethod
    def overlap(one: str, other: str) -> bool:
        """The same place, one inside the other, either way round."""
        one, other = one.rstrip(os.sep), other.rstrip(os.sep)
        return one == other or one.startswith(other + os.sep) or other.startswith(one + os.sep)

    def contested(self, path: str, rows: list[dict], entries: list[dict]) -> bool:
        """True when something that may still write overlaps the path: a job of a live voice-over, or a job or a
        submission on the journal that is not confirmed stopped (a file, the folder it writes in, or a folder above it)."""
        return (any(self.overlap(path, row["out_dir"]) for row in rows if row["status"] == S.ALIGNING and row["out_dir"])
                or any(self.overlap(path, entry["scope"]) for entry in entries
                       if entry["kind"] in (JOB, SUBMISSION) and entry["scope"]))

    async def sweep(self) -> None:
        """A pass over the whole journal; what does not end here stays with the recovery."""
        for entry in await self._repo.cleanup_list():
            if not await self.clear(entry["path"]):
                self._retry(entry["path"])

    async def work_off(self) -> None:
        """Carry out what has just been handed over; entries tried before belong to the recovery."""
        for entry in await self._repo.cleanup_list():
            if entry["error"] == HANDED_OVER and not await self.clear(entry["path"]):
                await self._repo.note_cleanup_error(entry["path"], "deferred", only_if=HANDED_OVER)
                self._retry(entry["path"])

    async def finish(self, entry: dict) -> bool:
        """End the entry in the version that was read: when the name was handed over again meanwhile, the entry
        stays for the pass that belongs to the new hand-over."""
        return await self._repo.cleanup_done(entry["path"], entry["version"])

    async def clear(self, path: str) -> bool:
        """One attempt to finish a journal entry; True when it is over. The name is owned before anything about it
        is looked at, so a voice-over that takes the name meanwhile waits for the end of the removal."""
        entry = next((entry for entry in await self._repo.cleanup_list() if entry["path"] == path), None)
        if entry is None:
            return True
        if entry["kind"] in (JOB, SUBMISSION):
            return await self.stop_job(entry)
        try:
            with storage.hold(path):
                return await self.remove(path)
        except storage.StorageBusy:
            return False  # another removal, or a call that timed out and has not ended, owns the name

    async def remove(self, path: str) -> bool:
        entries = await self._repo.cleanup_list()
        entry = next((entry for entry in entries if entry["path"] == path), None)
        if entry is None:
            return True
        rows = await self._repo.all_rows()
        if self.held(path, rows):
            return await self.finish(entry)  # the name belongs to a live voice-over again: removing it destroys new work
        if self.contested(path, rows, entries):
            return False  # the job that may write there ends first
        try:
            await self._io(self.unlink, path, entry["kind"], timeout=REMOVE_TIMEOUT_S, owns=path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            await self._repo.note_cleanup_error(path, str(exc))
            self._log.error("не удалось удалить %s, повторим: %s", path, exc)
            return False
        if not await self.finish(entry):
            return False
        self._log.info("очистка выполнена: %s", path)
        return True

    async def stop_job(self, entry: dict) -> bool:
        """A job nobody needs (named, or found by the key of a submission without an answer) is cancelled; only a
        confirmed stop ends the entry, and then the folder the job wrote in is cleaned."""
        job_id = entry["path"]
        if entry["kind"] == SUBMISSION:
            if entry["path"] in self._sending:
                return False  # the request is on its way: the service may take the job after any look at it
            try:
                found = await self._request("conform.job.find", {"key": entry["path"]})
            except (BusRequestError, asyncio.TimeoutError):
                return False
            if not found.get("reachable"):
                return False
            job_id = found.get("job_id")
        if job_id:
            try:
                reply = await self._request("conform.cancel", {"job_id": job_id})
            except (BusRequestError, asyncio.TimeoutError):
                return False
            if not reply.get("done"):
                return False
        scope = entry["scope"]
        if scope:
            await self._repo.journal_cleanup(scope, TREE, "job stopped")
        if not await self.finish(entry):
            return False
        self._poke("cleanup:")  # what waited for this job to stop may go on now
        if scope and not await self.clear(scope):
            self._retry(scope)
        elif scope:
            await self._io(prune, scope)
        return True
