"""The count of the life of a release of a torrent source (stage 15): its folder, its view, what the client's word of it
means, whether a page lists a newer version, and the task of the monitor each release gives.

Pure: the module `torrents` reads the base and the client, asks these functions what the facts mean, and acts on the answer.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from core.contracts.torrent import PageRelease, PageReleases, ReleaseFile, ReleaseState, ReleaseTask, ReleaseView
from core.fsnames import sanitize_name
from core.reasons import reason

from .states import CHECKING, torrent_flag

# The states of a release whose files are still on their way: the module follows each of them with the client.
MOVING = frozenset({ReleaseState.METADATA, ReleaseState.DOWNLOADING, ReleaseState.UPDATING})
# What the client does with the files of a torrent besides checking them: until it is done they are not all there yet.
FILES_BEING_MADE = CHECKING | {"moving", "allocating"}
# The states of the client that are a failure of the torrent, with the code of the reason the release fails with.
_BROKEN = {"error": "qbt_error", "missingFiles": "qbt_files_missing"}


def folder_name(site: str, url: str, release: str) -> str:
    """The folder of a release under the staging folder: the site and a key of the page and the release (an address is no
    name of a folder; the key is the same for every version, so an update finds the files of the one before)."""
    key = hashlib.md5(f"{url}\n{release}".encode()).hexdigest()[:12]
    return f"{sanitize_name(site) or 'tracker'}-{key}"


def relocated_folder(staging: str, folder: str) -> str:
    """Where a release's folder is once the staging folder moved: the same name under the new one."""
    return os.path.join(staging, os.path.basename(folder))


def view_of(row: Mapping, files: Iterable[Mapping], *, pending: bool, fresh_until: str | None, client: str | None) -> ReleaseView:
    """The release as the window and the neighbours see it: its row, its files, what waits for a sync, the next check of
    the schedule and what the client said of the torrent."""
    return ReleaseView(url=row["url"], release=row["release"], title=row["title"], published=row["published"],
                       infohash=row["infohash"], state=ReleaseState(row["state"]), reason=row["reason"],
                       watched=bool(row["watched"]), folder=row["folder"], pending=pending,
                       files=tuple(ReleaseFile(**each) for each in files), checked_at=row["checked_at"],
                       fresh_until=fresh_until, client=client)


def due(rows: Iterable[Mapping], last: datetime | None, among: set[str]) -> list[str]:
    """The watched pages of `among` not checked since the last check of the schedule (`last`; none: nothing is due)."""
    if last is None:
        return []
    return sorted({row["url"] for row in rows if row["watched"] and row["url"] in among
                   and (not row["checked_at"] or datetime.fromisoformat(row["checked_at"]) < last)})


def titled(page: PageReleases, release: PageRelease) -> PageRelease:
    """The name of a release as a window and the table show it: the title of the page, then the words of the release."""
    return release.model_copy(update={"title": " · ".join(part for part in (page.title, release.title) if part)})


def is_newer(row: Mapping, now: PageRelease) -> bool:
    """The page lists another version than the one kept: another infohash where the page names it, else another date."""
    if now.infohash and row["infohash"]:
        return now.infohash.lower() != row["infohash"].lower()
    return bool(now.published) and now.published != row["published"]


def wants_update(row: Mapping, now: PageRelease | None) -> bool:
    """A kept release begins an update when it is at rest (`ready` or `failed`), the page still lists it and lists it newer."""
    return row["state"] in (ReleaseState.READY, ReleaseState.FAILED) and now is not None and is_newer(row, now)


def status_flag(view: ReleaseView) -> str | None:
    """The flag of the status of a series that a release gives (calc/catalog): what the client does with it (a check, a
    download, a wait) while it is not ready, `ready` when it is, an error when it failed; an archived one gives none."""
    if view.state == ReleaseState.FAILED:
        return "error"
    if view.state == ReleaseState.ARCHIVED:
        return None
    if view.state == ReleaseState.METADATA:
        return "metadata"
    ready = view.state == ReleaseState.READY
    return torrent_flag(view.client or ("uploading" if ready else "downloading"), ready)


# --- what one look at a moving release came to -----------------------------------------------------------------------

def failure_of(info: Mapping | None) -> str | None:
    """The reason a release fails with when the client no longer has its torrent or says it is broken; none while it is fine."""
    if info is None:
        return reason("torrent_gone_from_client")
    code = _BROKEN.get(info.get("state"))
    return reason(code) if code else None


def files_of(listed: Iterable[Mapping]) -> list[dict]:
    """The files of a torrent as the client lists them, in the form the release keeps them."""
    return [{"path": f["name"], "size": f["size"], "progress": f["progress"], "priority": f["priority"]} for f in listed]


@dataclass(frozen=True, slots=True)
class Followed:
    """What a look at a moving release came to: whether the files kept are to be written again, the state it moves to
    (none: it stays), whether the window is told, and whether the files of the old version are removed (an update done)."""

    changed: bool
    state: ReleaseState | None
    told: bool
    purge: bool


def followed(state: ReleaseState, folder: str | None, client_state: str | None, files: Sequence[Mapping],
             before: Sequence[Mapping]) -> Followed:
    """The files a torrent lists now against those kept, and what the client does with it: the first files of a magnet start the
    download; every wanted file there, with no check or move going on, is the end of the download or of the update."""
    changed = list(files) != list(before)
    if state == ReleaseState.METADATA and files:
        return Followed(changed, ReleaseState.DOWNLOADING, True, False)
    wanted = [f for f in files if f["priority"] > 0]
    done = bool(files) and client_state not in FILES_BEING_MADE and all(f["progress"] >= 1 for f in wanted)
    if not done:
        return Followed(changed, None, changed, False)
    return Followed(changed, ReleaseState.READY, True, state == ReleaseState.UPDATING and bool(folder))


# --- the monitor of the window ---------------------------------------------------------------------------------------

def release_tasks(views: Iterable[ReleaseView], series_of: Mapping[str, Sequence[tuple[int, str | None]]]) -> tuple[ReleaseTask, ...]:
    """The tasks the monitor of «Агенты» shows for the releases: one per series whose torrent source is the page (one
    with no series when none lists it yet), the progress by the size of the wanted files."""
    tasks = []
    for view in views:
        wanted = [f for f in view.files if f.priority > 0]
        size = sum(f.size for f in wanted)
        progress = round(100 * sum(f.size * f.progress for f in wanted) / size, 1) if size else 0.0
        for series_id, series_name in list(series_of.get(view.url, ())) or [(None, "")]:
            tasks.append(ReleaseTask(
                kind="release", qb_hash=view.infohash or "", url=view.url, release=view.release, title=view.title,
                series_id=series_id, series_name=series_name or "", status=view.state.value, progress=progress,
                files_done=sum(1 for f in wanted if f.progress >= 1), files_total=len(wanted), error_message=view.reason))
    return tuple(tasks)
