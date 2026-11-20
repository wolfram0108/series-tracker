"""The life of a release of a torrent source (stage 15): the download into the staging folder, the versions, the hold of an
update while a sync of a series with the source runs, the files and the forgetting.

A release is keyed by its page and its id on the page, never by a series: one page in two series is one download. Its
files lie under their own names in its folder (`<staging>/<site>-<key>`); the source is in a series only once every
wanted file is there (`ready`). An update is the new version added started in the same folder: the client checks the files
on disk itself (measured, DESIGN.md «Обновление в qBittorrent»), downloads only what is not there, and the release is
unavailable as a whole until it is `ready` again; the files of the old version the new one does not list are removed then.
Nothing waits by time: what the client does is read from it (its WebAPI pushes nothing) while some release moves or is
being checked, and now and then otherwise, so that a check the client began by itself is told too.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from core import ids, storage
from core.contracts.availability import NotAllowed
from core.contracts.torrent import (RELEASE_CHANGED_TOPIC, PageRelease, PageReleases, ReleaseFile, ReleaseState,
                                    ReleaseView)
from core.fsnames import sanitize_name
from core.logging import get_logger
from core.topics import SOURCES_RELEASES_GET, SOURCES_TORRENT_FILE_GET
from core.reasons import reason
from core.schedule import next_at

from .infohash import TorrentParseError, infohash_candidates, infohash_from_magnet
from .pipeline import CHECKING, torrent_flag
from .qbt_client import QbtClient
from .repository import ReleaseStore

CATEGORY = "st-source"
INCOMPLETE = ".!qB"
MOVING = {ReleaseState.METADATA, ReleaseState.DOWNLOADING, ReleaseState.UPDATING}
_CHECKING = {"checkingUP", "checkingDL", "checkingResumeData", "moving", "allocating"}
_BROKEN = {"error": "qbt_error", "missingFiles": "qbt_files_missing"}

Request = Callable[..., Awaitable[Any]]


def folder_name(site: str, url: str, release: str) -> str:
    """The folder of a release under the staging folder: the site and a key of the page and the release (an address is no
    name of a folder; the key is the same for every version, so an update finds the files of the one before)."""
    key = hashlib.md5(f"{url}\n{release}".encode()).hexdigest()[:12]
    return f"{sanitize_name(site) or 'tracker'}-{key}"


def _orphans(folder: str, listed: set[str]) -> list[str]:
    """The files under the folder the version does not list (by its name or the client's name of an incomplete one)."""
    if not os.path.isdir(folder):
        return []
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.relpath(os.path.join(root, name), folder)
            plain = path[:-len(INCOMPLETE)] if path.endswith(INCOMPLETE) else path
            if plain not in listed:
                found.append(os.path.join(folder, path))
    return found


def _remove_orphans(folder: str, listed: set[str]) -> list[str]:
    gone = []
    for path in _orphans(folder, listed):
        if storage.remove_file(path):
            gone.append(path)
    for root, dirs, files in os.walk(folder, topdown=False):
        if root != folder and not dirs and not files:
            os.rmdir(root)
    return gone


@dataclass
class Releases:
    """The releases of the module `torrents`; the module gives it the client, the bus and where its events go."""

    store: ReleaseStore
    client: Callable[[], QbtClient | None]
    reply: Request  # `BaseModule.reply_of`: the answer of a neighbour as the model of its topic
    publish: Callable[[str, dict], None]
    wake: Callable[[], None]
    staging: str = ""
    log: Any = field(default_factory=lambda: get_logger("torrents"))
    # Series whose sync runs now, with the pages of their sources: an update of such a page waits for the end of the run.
    runs: dict[int, set[str]] = field(default_factory=dict)
    # Releases whose newer version waits for a run to end: (url, release) → the page release.
    pending: dict[tuple[str, str], PageRelease] = field(default_factory=dict)
    # The minutes of the day the watched pages are checked (the schedule, core/schedule.py).
    checks: tuple[int, ...] = ()
    # What the client said of each torrent at its last asking: infohash → its state (a fact of the moment, not stored).
    clients: dict[str, str] = field(default_factory=dict)

    # --- what is asked ---------------------------------------------------------------------------------------------------

    async def view(self, row: dict) -> ReleaseView:
        files = [ReleaseFile(**f) for f in await self.store.files(row["id"])]
        return ReleaseView(url=row["url"], release=row["release"], title=row["title"], published=row["published"],
                           infohash=row["infohash"], state=ReleaseState(row["state"]), reason=row["reason"],
                           watched=bool(row["watched"]), folder=row["folder"],
                           pending=(row["url"], row["release"]) in self.pending, files=files,
                           checked_at=row["checked_at"], fresh_until=self.fresh_until(row["checked_at"]),
                           client=self.clients.get(row["infohash"]) if row["infohash"] else None)

    def fresh_until(self, checked_at: str | None) -> str | None:
        """The next check of the schedule after the last one: past it the release is not known to be current."""
        if not checked_at:
            return None
        found = next_at(self.checks, datetime.fromisoformat(checked_at))
        return found.astimezone(timezone.utc).isoformat() if found is not None else None

    async def due(self, last: datetime | None, among: set[str]) -> list[str]:
        """The watched pages of `among` not checked since the last check of the schedule (`last`; none: nothing is due)."""
        if last is None:
            return []
        rows = [row for row in await self.store.all() if row["watched"] and row["url"] in among]
        return sorted({row["url"] for row in rows
                       if not row["checked_at"] or datetime.fromisoformat(row["checked_at"]) < last})

    async def told_all(self) -> None:
        """Every release is told again: the schedule moved the next check of each."""
        for row in await self.store.all():
            await self._told(row["url"], row["release"])

    async def of_page(self, url: str) -> list[ReleaseView]:
        return [await self.view(row) for row in await self.store.of_page(url)]

    async def _told(self, url: str, release: str) -> None:
        row = await self.store.get(url, release)
        if row is not None:
            self.publish(RELEASE_CHANGED_TOPIC, (await self.view(row)).model_dump(mode="json"))

    # --- the download and the update -------------------------------------------------------------------------------

    def _client(self) -> QbtClient:
        qbt = self.client()
        if qbt is None:
            raise NotAllowed(reason("qbt_not_configured"))
        return qbt

    async def _page(self, url: str) -> PageReleases:
        return await self.reply(SOURCES_RELEASES_GET, {"url": url})

    async def download(self, url: str, release: str) -> ReleaseView:
        """The user's «Скачать»: the release as the page lists it now goes to the client, into its folder."""
        if not self.staging:
            raise NotAllowed(reason("staging_dir_not_set"))
        page = await self._page(url)
        found = next((each for each in page.releases if each.release == release), None)
        if found is None:
            raise NotAllowed(reason("release_not_on_page", release=release))
        found = _titled(page, found)
        row = await self.store.get(page.url, release)
        if row is not None and row["state"] not in (ReleaseState.FAILED, ReleaseState.ARCHIVED):
            return await self.view(row)
        folder = os.path.join(self.staging, folder_name(page.site, page.url, release))
        await self._add(page.url, found, folder, first=True)
        return await self.view(await self.store.get(page.url, release))

    async def _add(self, url: str, page_release: PageRelease, folder: str, *, first: bool) -> None:
        """The version the page lists goes to the client, started, into `folder`; recorded before anything is told."""
        qbt = self._client()
        if page_release.link:
            got = await self.reply(SOURCES_TORRENT_FILE_GET, {
                "url": page_release.link, "torrent_id": ids.torrent_id(page_release.link, page_release.published)})
            content = base64.b64decode(got.content_b64)
            try:
                candidates = infohash_candidates(content)
            except TorrentParseError as exc:
                raise NotAllowed(reason("torrent_file_broken", detail=str(exc))) from exc
            await qbt.add_torrent_file(content, folder, paused=False, category=CATEGORY)
            infohash = await qbt.find_hash(candidates) or candidates[0]
            state = ReleaseState.DOWNLOADING if first else ReleaseState.UPDATING
        elif page_release.magnet:
            infohash = infohash_from_magnet(page_release.magnet)
            await qbt.add_magnet(page_release.magnet, folder, paused=False, category=CATEGORY)
            state = ReleaseState.METADATA if first else ReleaseState.UPDATING
        else:
            raise NotAllowed(reason("release_without_torrent", release=page_release.release))
        release_id = await self.store.save(url, page_release.release, title=page_release.title,
                                           link=page_release.link or page_release.magnet or "",
                                           published=page_release.published, infohash=infohash, state=state.value,
                                           reason=None, folder=folder)
        await self.store.add_version(release_id, infohash, page_release.published)
        await self._told(url, page_release.release)
        self.wake()

    def held(self, url: str) -> bool:
        return any(url in pages for pages in self.runs.values())

    async def check(self, url: str) -> list[str]:
        """Read the page now; every watched release whose version the page lists is newer begins its update, or waits for
        the end of the run of a series with this source. Every watched release of the page is checked now (`checked_at`)
        and told once, with what the check found. Returns the releases whose update began or waits."""
        watched = [row for row in await self.store.of_page(url) if row["watched"]]
        if not watched:
            return []
        page = await self._page(url)
        checked_at = datetime.now(timezone.utc).isoformat()
        for row in watched:
            await self.store.save(url, row["release"], checked_at=checked_at)
        listed = {each.release: _titled(page, each) for each in page.releases}
        newer = []
        for row in watched:
            now = listed.get(row["release"])
            if row["state"] not in (ReleaseState.READY, ReleaseState.FAILED) or now is None or not _is_newer(row, now):
                await self._told(url, row["release"])
                continue
            newer.append(row["release"])
            if self.held(url):
                self.pending[(url, row["release"])] = now
                await self._told(url, row["release"])
            else:
                await self._update(row, now)
        return newer

    async def _update(self, row: dict, now: PageRelease) -> None:
        qbt = self._client()
        self.pending.pop((row["url"], row["release"]), None)
        if row["infohash"]:
            await qbt.delete([row["infohash"]], delete_files=False)
        self.log.info("раздача %s %s: новая версия %s", row["url"], row["release"], now.published)
        await self._add(row["url"], now, row["folder"], first=False)

    async def run_changed(self, series_id: int, active: bool, pages: set[str]) -> None:
        """A sync of a series opened or closed; at its close the updates it held begin."""
        if active:
            self.runs[series_id] = pages
            return
        self.runs.pop(series_id, None)
        for (url, release), now in list(self.pending.items()):
            if self.held(url):
                continue
            row = await self.store.get(url, release)
            if row is None:
                self.pending.pop((url, release), None)
                continue
            await self._update(row, now)

    # --- what the client does ------------------------------------------------------------------------------------------

    async def tick(self) -> bool:
        """What the client says of every release it holds: told when it changed (a check began or ended), and for a release
        that moves its files and the end of its download. True while one moves or is checked: the client is asked soon again."""
        rows = [row for row in await self.store.all() if row["infohash"] and row["state"] != ReleaseState.ARCHIVED]
        qbt = self.client()
        if not rows or qbt is None:
            return False
        info = {each["hash"]: each for each in await qbt.torrents_info([row["infohash"] for row in rows])}
        busy = False
        for row in rows:
            found = info.get(row["infohash"])
            state = found.get("state") if found else None
            before = self.clients.get(row["infohash"])
            if state is None:
                self.clients.pop(row["infohash"], None)
            else:
                self.clients[row["infohash"]] = state
            if ReleaseState(row["state"]) in MOVING:
                await self._follow(qbt, row, found)
                busy = True
            if state != before:
                await self._told(row["url"], row["release"])
            busy = busy or state in CHECKING
        return busy

    async def _follow(self, qbt: QbtClient, row: dict, info: dict | None) -> None:
        url, release = row["url"], row["release"]
        if info is None:
            await self.store.save(url, release, state=ReleaseState.FAILED.value, reason=reason("torrent_gone_from_client"))
            await self._told(url, release)
            return
        if info.get("state") in _BROKEN:
            await self.store.save(url, release, state=ReleaseState.FAILED.value, reason=reason(_BROKEN[info["state"]]))
            await self._told(url, release)
            return
        listed = await qbt.torrent_files(row["infohash"]) or []
        files = [{"path": f["name"], "size": f["size"], "progress": f["progress"], "priority": f["priority"]}
                 for f in listed]
        before = await self.store.files(row["id"])
        if files != before:
            await self.store.set_files(row["id"], files)
        state = ReleaseState(row["state"])
        if state == ReleaseState.METADATA and files:
            await self.store.save(url, release, state=ReleaseState.DOWNLOADING.value)
            await self._told(url, release)
            return
        wanted = [f for f in files if f["priority"] > 0]
        done = bool(files) and info.get("state") not in _CHECKING and all(f["progress"] >= 1 for f in wanted)
        if not done:
            if files != before:
                await self._told(url, release)
            return
        if state == ReleaseState.UPDATING and row["folder"]:
            gone = await storage.call(_remove_orphans, row["folder"], {f["path"] for f in files},
                                      timeout=storage.REMOVE_TIMEOUT_S, owns=row["folder"])
            if gone:
                self.log.info("раздача %s %s: убраны файлы прежней версии: %d", url, release, len(gone))
        await self.store.save(url, release, state=ReleaseState.READY.value, reason=None)
        await self._told(url, release)

    # --- what the user and the other modules ask -------------------------------------------------------------------

    async def watch(self, url: str, release: str, watched: bool) -> ReleaseView:
        row = await self._row(url, release)
        await self.store.save(url, release, watched=int(watched))
        if not watched:
            self.pending.pop((url, release), None)
        await self._told(url, release)
        return await self.view(await self.store.get(url, release) or row)

    async def drop_files(self, url: str, release: str) -> None:
        """A release no longer watched gives its files up once what was taken from it lives in the output files: it leaves
        the client with them and stays as a record (`archived`); taking it again is a new download."""
        row = await self._row(url, release)
        await self._remove(row)
        await self.store.save(url, release, state=ReleaseState.ARCHIVED.value, infohash=None)
        await self.store.set_files(row["id"], [])
        await self._told(url, release)

    async def forget(self, url: str) -> None:
        """No source lists the page any more: its releases leave the client with their files and are forgotten."""
        for row in await self.store.of_page(url):
            await self._remove(row)
            await self.store.delete(row["id"])
            self.pending.pop((url, row["release"]), None)
            self.log.info("раздача %s %s забыта вместе с файлами", url, row["release"])

    async def _remove(self, row: dict) -> None:
        if row["infohash"] and self.client() is not None:
            await self._client().delete([row["infohash"]], delete_files=True)
        if row["folder"]:
            await storage.call(shutil.rmtree, row["folder"], ignore_errors=True, timeout=storage.REMOVE_TIMEOUT_S,
                               owns=row["folder"])

    async def _row(self, url: str, release: str) -> dict:
        row = await self.store.get(url, release)
        if row is None:
            raise NotAllowed(reason("release_not_kept", release=release))
        return row

    async def relocate(self, staging: str) -> None:
        """The staging folder of the settings changed: every release's folder moves there with the client's help."""
        old, self.staging = self.staging, staging
        if not old or old == staging:
            return
        qbt = self.client()
        for row in await self.store.all():
            if not row["folder"]:
                continue
            folder = os.path.join(staging, os.path.basename(row["folder"]))
            if row["infohash"] and qbt is not None:
                await qbt.set_location(row["infohash"], folder)
            await self.store.save(row["url"], row["release"], folder=folder)


def status_flag(view: ReleaseView) -> str | None:
    """The flag of the status of a series that a release gives (modules/catalog/aggregator.py): what the client does with it
    (a check, a download, a wait) while it is not ready, `ready` when it is, an error when it failed; an archived one gives none."""
    if view.state == ReleaseState.FAILED:
        return "error"
    if view.state == ReleaseState.ARCHIVED:
        return None
    if view.state == ReleaseState.METADATA:
        return "metadata"
    ready = view.state == ReleaseState.READY
    return torrent_flag(view.client or ("uploading" if ready else "downloading"), ready)


def _titled(page: PageReleases, release: PageRelease) -> PageRelease:
    """The name of a release as a window and the table show it: the title of the page, then the words of the release."""
    return release.model_copy(update={"title": " · ".join(part for part in (page.title, release.title) if part)})


def _is_newer(row: dict, now: PageRelease) -> bool:
    """The page lists another version than the one kept: another infohash where the page names it, else another date."""
    if now.infohash and row["infohash"]:
        return now.infohash.lower() != row["infohash"].lower()
    return bool(now.published) and now.published != row["published"]
