"""The module unpack — the reading of the releases of the torrent sources (stage 15): what every file holds, the layout of
the torrent editor, and the rows of the table of a tracker's page. Tables of the module: unpack_streams, unpack_folders,
unpack_assignments, unpack_hidden, unpack_removed, unpack_sources (modules/unpack/repository.py).

A release `torrents` downloaded is read here: every video, sound and subtitles file by ffprobe once per version and size
(the video of a source is never compared by content); the files are laid out by the rules of the five tabs of the editor
and by what the user placed by hand (calc/unpack/layout.py). The editor places, it never leaves out: an item is placed
(green) or not (red); every placed sound and subtitles track of a placed video is a row of the table of the page, as a row
of a site, and a red one is no row — what goes out of the output is the work of the constructor of the series. The one
exception is a subtitles track with no events at all (only a header; the read counts the packets of each subtitles stream):
it has nothing to show and a container cannot be built with it, so it is no row; the tracks not counted are kept. Every file
of a release is downloaded: nothing here tells `torrents` what not to download. Every tab of the editor of a source («Файлы», «Озвучки», «Субтитры»,
«Озв. конт.», «Суб. конт.») has its own profile of rules (the kind `torrent`), empty until the owner writes rules in it, made when the
editor is first opened; nothing is placed by rules no one wrote. A page no torrent source lists any more is forgotten with
its profiles.

The layout is counted in the pool of computation (calc/unpack): the lines of the files, then the layout itself; between them
the rules of each tab are asked, because the tabs of tracks read the places the files got.

Queries / commands (a page is named by any mirror of its tracker; the module keeps it, and answers it, in its one spelling):
  unpack.table.get {url} → {names, rows} — the rows of the page (`sources` asks it as the read of a tracker's
      page): a release downloaded for the first time gives none, a release being updated gives its rows not obtained
  unpack.editor.open {url} → {url, profiles} — the editor of the page is made ready: the profiles of its five tabs are made
      when they are missing (the first opening of the page) and kept; the window asks it before it asks for the editor
  unpack.editor.get {url} → {url, profiles: {files, voices, subtitles, voice_streams, subtitle_streams}, view} — the editor of the page: its releases,
      the items of every tab with their line, their place or folder and what put them there, the folders, what is unplaced;
      it changes nothing (a tab whose profile was not made has none in `profiles`)
  unpack.assign {url, tab, items, season?, episode?, episode_last?, tag?, folder?, in_order?} — placed by hand (a drag): a
      file in a season and an episode (of the version `tag`, DIR), a track in a folder; `in_order`: the items in the order given take the episodes from
      `episode` on, one each (files dropped on a season)
  unpack.unassign {url, tab, items} — back to what the rules give
  unpack.folder.set {url, tab, name, language?, subtitle_type?} / unpack.folder.delete {url, tab, name, automatic?, items?}
      — a folder belongs to its tab of tracks («Озвучки», «Субтитры», «Озв. конт.», «Суб. конт.» are four sets); one deleted
      takes the hand's placements in it (`items`) and, holding tracks of the automatic layout (`automatic`: the titles in the
      files, the rules), is not made by it again until «Авто»
  unpack.remove {url, tab, items} — «−»: the tracks are taken out of their folders, red, whatever the files and the rules
      say (a hand's placement goes too), until placed by hand or «Авто»
  unpack.auto {url, tabs} — «Авто»: the automatic layout makes its deleted folders of these tabs again and places again the
      tracks taken out of folders; the hand's placements stay
Events out:
  unpack.table.changed {url} — the rows of the page changed (`sources` reads its table again); told of every page the
      module keeps when it starts: a read of a tracker's page begun before it (resumed by `sources` at the start) found
      no one to ask
  unpack.editor.changed {url} — the editor of the page changed (a window shows it again)
  unpack.read.progress {url, release, done, total} — the files of a version read so far, while they are read; the editor
      gives the same as `reading` of a release, for a window opened in the middle of a read
Events in: torrents.release.changed (a release with its files: a ready one is read; a news that changes only how far the
  files are downloaded is passed by), rules.profile.changed (a profile of the editor changed),
  series.source.added / updated / removed and series.deleted (a page no source lists any more is forgotten).
"""
from __future__ import annotations

import os
from dataclasses import replace

from calc.unpack.layout import Inputs, lines
from calc.unpack.read import files_to_read, shape_of, subtitle_tracks, with_events
from calc.unpack.reply import (PROFILE_COLUMNS, editor_reply, profile_name, profiles_of, results_of, stored_inputs,
                               table_reply, torrent_urls)
from core import compute
from core.contracts import RulesApplyReply
from core.contracts.torrent import EditorTab, ReleaseState, ReleaseView
from core.db import Database
from core.envelope import Envelope
from core.ffprobe import layout_of, subtitle_events
from core.module import BaseModule
from core.topics import (CATALOG_SERIES_LIST, RULES_APPLY, RULES_PROFILE_CHANGED, RULES_PROFILES_CREATE,
                         RULES_PROFILES_DELETE, SERIES_DELETED, SERIES_SOURCE_ADDED, SERIES_SOURCE_REMOVED,
                         SERIES_SOURCE_UPDATED, SOURCES_SITE_RESOLVE, TORRENTS_RELEASE_CHANGED, TORRENTS_RELEASES_GET,
                         UNPACK_ASSIGN, UNPACK_AUTO, UNPACK_EDITOR_CHANGED, UNPACK_EDITOR_GET, UNPACK_EDITOR_OPEN,
                         UNPACK_FOLDER_DELETE, UNPACK_FOLDER_SET, UNPACK_READ_PROGRESS, UNPACK_REMOVE, UNPACK_TABLE_CHANGED,
                         UNPACK_TABLE_GET, UNPACK_UNASSIGN)

from .repository import UnpackRepository


class UnpackModule(BaseModule):
    name = "unpack"

    def __init__(self, bus, db: Database) -> None:
        self.repo = UnpackRepository(db)
        self._reading: dict[tuple[str, str], tuple[int, int]] = {}  # (url, release) → (files read, files to read)
        # What the module last took of each release: a news that changes only how far its files are is none of its business.
        self._shapes: dict[tuple[str, str], tuple] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(UNPACK_TABLE_GET, self.on_table_get)
        self.handle(UNPACK_EDITOR_OPEN, self.on_editor_open)
        self.handle(UNPACK_EDITOR_GET, self.on_editor_get)
        self.handle(UNPACK_ASSIGN, self.on_assign)
        self.handle(UNPACK_UNASSIGN, self.on_unassign)
        self.handle(UNPACK_FOLDER_SET, self.on_folder_set)
        self.handle(UNPACK_FOLDER_DELETE, self.on_folder_delete)
        self.handle(UNPACK_AUTO, self.on_auto)
        self.handle(UNPACK_REMOVE, self.on_remove)
        self.handle(TORRENTS_RELEASE_CHANGED, self.on_release_changed)
        self.handle(RULES_PROFILE_CHANGED, self.on_profile_changed)
        for topic in (SERIES_SOURCE_ADDED, SERIES_SOURCE_UPDATED, SERIES_SOURCE_REMOVED, SERIES_DELETED):
            self.handle(topic, self.on_source_changed)

    async def on_start(self) -> None:
        try:
            await self._forget_unlisted()
        except Exception:  # noqa: BLE001 - what no source lists any more is forgotten at the next change of the sources
            self.log.exception("the pages no source lists were not forgotten")
        # The rows of the pages can be asked now: a read of one that began before (a start) is made again from them.
        for url in await self.repo.pages():
            self.publish_event(UNPACK_TABLE_CHANGED, {"url": url})
        self.spawn(self._read_unfinished(), name="unpack:read")

    async def _read_unfinished(self) -> None:
        """The releases the database holds as read before the events of their subtitles were counted are read again, the
        way a change of them reads them (`_read` takes only the files that need it)."""
        try:
            for url in await self.repo.pages():
                for release in await self._releases(url):
                    if release.state == ReleaseState.READY and release.folder:
                        await self._read(release)
                self._changed(url)
            self.log.info("релизы, разобранные до подсчёта реплик субтитров, перечитаны")
        except Exception:  # noqa: BLE001 - what is not read now is read at the next change of its release
            self.log.exception("the releases read before the events were counted were not read again")

    async def _forget_unlisted(self) -> None:
        """A page no torrent source of any series lists any more: what the module kept of it goes, its profiles too."""
        listed = set(await self._pages())
        for url in await self.repo.pages():
            if url in listed:
                continue
            stored = await self.repo.profiles(url) or {}
            await self.repo.forget(url)
            for profile_id in profiles_of(stored).values():
                await self.request(RULES_PROFILES_DELETE, {"profile_id": profile_id})
            self.log.info("страница %s больше не источник: её разбор и профили вкладок забыты", url)

    async def on_source_changed(self, env: Envelope) -> None:
        await self._forget_unlisted()

    # --- the layout ---------------------------------------------------------------------------------------------------

    async def _releases(self, url: str) -> list[ReleaseView]:
        reply = await self.request(TORRENTS_RELEASES_GET, {"url": url})
        return [ReleaseView.model_validate(each) for each in reply.get("releases") or []]

    async def _apply(self, profile_id: int | None, keyed: dict[str, str]) -> dict[str, dict]:
        """The rules of a tab applied to its lines: the result of each, by its item."""
        if not profile_id or not keyed:
            return {}
        reply: RulesApplyReply = await self.reply_of(RULES_APPLY, {
            "profile_id": profile_id, "lines": [{"key": key, "text": text} for key, text in keyed.items()]})
        return await compute.run(results_of, reply)

    async def _inputs(self, url: str, releases: list[ReleaseView] | None = None) -> Inputs:
        """The inputs of the layout of a page: what is stored, then the rules of «Файлы» over the lines of the files, then
        the rules of the tabs of tracks over the lines that read the places the files got."""
        releases = releases if releases is not None else await self._releases(url)
        streams = {r.release: await self.repo.streams(url, r.release) for r in releases}
        inputs = stored_inputs(releases, streams, await self.repo.assignments(url), await self.repo.folders(url),
                               await self.repo.hidden(url), await self.repo.removed(url))
        profiles = await self.repo.profiles(url) or {}
        keyed = await compute.run(lines, inputs)
        results = {EditorTab.FILES: await self._apply(profiles.get(PROFILE_COLUMNS["files"]), keyed[EditorTab.FILES])}
        keyed = await compute.run(lines, replace(inputs, results=dict(results)))  # the tabs of tracks read the places the files got
        for tab in (EditorTab.VOICES, EditorTab.SUBTITLES, EditorTab.VOICE_STREAMS, EditorTab.SUBTITLE_STREAMS):
            results[tab] = await self._apply(profiles.get(PROFILE_COLUMNS[tab.value]), keyed[tab])
        return replace(inputs, results=results)

    async def _page(self, url: str) -> str:
        """The one spelling of a page (its tracker's own domain): what the module keeps of a page is keyed by it, whatever
        mirror the source of a series was written with."""
        resolved = await self.reply_of(SOURCES_SITE_RESOLVE, {"url": url})
        return resolved.canonical_url or url

    async def on_table_get(self, env: Envelope) -> dict:
        url = await self._page(env.payload["url"])
        return await compute.run(table_reply, await self._inputs(url))

    async def on_editor_get(self, env: Envelope) -> dict:
        url = await self._page(env.payload["url"])
        stored = await self.repo.profiles(url)
        inputs = await self._inputs(url)
        return await compute.run(editor_reply, url, stored, inputs, dict(self._reading))

    async def on_editor_open(self, env: Envelope) -> dict:
        """The profiles of the five tabs of the source, each its own and empty until the owner writes rules in it; made when
        its editor is first opened (a tab missing from an older page gets its profile then)."""
        url = await self._page(env.payload["url"])
        stored = await self.repo.profiles(url) or {}
        made = {}
        for tab, column in PROFILE_COLUMNS.items():
            made[tab] = stored.get(column) or (await self.request(RULES_PROFILES_CREATE, {
                "name": profile_name(url, tab), "kind": "torrent"}))["id"]
        if any(stored.get(column) != made[tab] for tab, column in PROFILE_COLUMNS.items()):
            await self.repo.save_profiles(url, made)
        return {"url": url, "profiles": made}

    def _changed(self, url: str) -> None:
        self.publish_event(UNPACK_TABLE_CHANGED, {"url": url})
        self.publish_event(UNPACK_EDITOR_CHANGED, {"url": url})

    # --- what the user places -------------------------------------------------------------------------------------

    async def on_assign(self, env: Envelope) -> None:
        p, url = env.payload, await self._page(env.payload["url"])
        await self.repo.assign(url, p["tab"], list(p["items"]), season=p.get("season"), episode=p.get("episode"),
                               episode_last=p.get("episode_last"), folder=p.get("folder"), in_order=bool(p.get("in_order")),
                               tag=p.get("tag") or "")
        self._changed(url)

    async def on_unassign(self, env: Envelope) -> None:
        url = await self._page(env.payload["url"])
        await self.repo.unassign(url, env.payload["tab"], list(env.payload["items"]))
        self._changed(url)

    async def on_folder_set(self, env: Envelope) -> None:
        p, url = env.payload, await self._page(env.payload["url"])
        await self.repo.set_folder(url, p["tab"], p["name"], p.get("language") or "", p.get("subtitle_type") or "")
        await self.repo.unhide(url, [p["tab"]], p["name"])  # a folder the user names is his, whatever the layout did before
        self._changed(url)

    async def on_folder_delete(self, env: Envelope) -> None:
        url = await self._page(env.payload["url"])
        await self.repo.delete_folder(url, env.payload["tab"], env.payload["name"], list(env.payload.get("items") or []),
                                      bool(env.payload.get("automatic")))
        self._changed(url)

    async def on_remove(self, env: Envelope) -> None:
        url = await self._page(env.payload["url"])
        await self.repo.remove(url, env.payload["tab"], list(env.payload["items"]))
        self._changed(url)

    async def on_auto(self, env: Envelope) -> None:
        url = await self._page(env.payload["url"])
        await self.repo.unhide(url, list(env.payload["tabs"]))
        self._changed(url)

    # --- what torrents and rules tell ------------------------------------------------------------------------------

    async def on_release_changed(self, env: Envelope) -> None:
        release = ReleaseView.model_validate(env.payload)
        shape = shape_of(release)
        if self._shapes.get((release.url, release.release)) == shape:
            return
        self._shapes[(release.url, release.release)] = shape
        if release.state == ReleaseState.READY and release.folder:
            await self._read(release)
        self._changed(release.url)

    async def _read(self, release: ReleaseView) -> None:
        """Every video, sound and subtitles file of the version, read once per version and size."""
        todo = files_to_read(release, await self.repo.streams(release.url, release.release))
        key = (release.url, release.release)
        try:
            for done, f in enumerate(todo):
                self._progress(key, done, len(todo))
                path = os.path.join(release.folder, f.path)
                layout, said = await layout_of(path)
                if layout is not None:
                    layout = await self._count_events(f.path, path, layout)
                await self.repo.save_streams(release.url, release.release, f.path, release.infohash or "", f.size, layout,
                                             None if layout is not None else said)
        finally:
            if todo:
                self._progress(key, len(todo), len(todo))
            self._reading.pop(key, None)
        await self.repo.drop_streams(release.url, release.release, {f.path for f in release.files})

    async def _count_events(self, name: str, path: str, layout: dict) -> dict:
        """Each subtitles stream of the file gets its number of events. When they could not be counted the streams keep none
        (the tracks are taken as they are: only a track known to hold none is left out) and the file is counted again at its
        next read: a count that failed is not an answer to keep."""
        if not subtitle_tracks(layout):
            return layout
        counts = await subtitle_events(path)
        if counts is None:
            self.log.warning("в файле %s реплики дорожек субтитров не посчитаны: будут посчитаны при следующем чтении", name)
            return layout
        counted, empty = with_events(layout, counts)
        for s in empty:
            self.log.info("в файле %s дорожка субтитров %d (%s) без реплик: в таблицу не попадёт", name, s["index"],
                          s["title"] or s["language"] or "без названия")
        return counted

    def _progress(self, key: tuple[str, str], done: int, total: int) -> None:
        self._reading[key] = (done, total)
        self.publish_event(UNPACK_READ_PROGRESS, {"url": key[0], "release": key[1], "done": done, "total": total})

    async def on_profile_changed(self, env: Envelope) -> None:
        profile_id = env.payload.get("profile_id")
        for url in await self._pages():
            if profile_id in profiles_of(await self.repo.profiles(url)).values():
                self._changed(url)

    async def _pages(self) -> list[str]:
        """The pages of the torrent sources of every series, each in its one spelling."""
        pages = set()
        for url in torrent_urls((await self.reply_of(CATALOG_SERIES_LIST, {})).series):
            pages.add(await self._page(url))
        return sorted(pages)
