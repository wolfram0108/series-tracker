"""The module unpack — the reading of the releases of the torrent sources (stage 15): what every file holds, the layout of
the torrent editor, and the rows of the table of a tracker's page. Tables of the module: unpack_streams, unpack_folders,
unpack_assignments, unpack_sources (modules/unpack/repository.py).

A release `torrents` downloaded is read here: every video, sound and subtitles file by ffprobe once per version and size
(the video of a source is never compared by content); the files are laid out by the rules of the four tabs of the editor
and by what the user placed by hand (modules/unpack/layout.py), and every sound and subtitles track of a placed video is a
row of the table of the page, as a row of a site. The rules of the tab «Файлы» are one profile for every source (the preset
of the torrent editor, setting `unpack.files_profile_id`); the tabs «Озвучки», «Субтитры», «Контейнеры» have profiles of
each source, made when its editor is first opened. Both are profiles of the kind `torrent` (rules).

Queries / commands:
  unpack.table.get {url} → TableDraft {names, title, rows} — the rows of the page (`sources` asks it as the read of a tracker's
      page): a release downloaded for the first time gives none, a release being updated gives its rows not obtained
  unpack.editor.get {url} → {url, profiles: {files, voices, subtitles, streams}, view} — the editor of the page: its releases,
      the items of every tab with their line, their place or folder and what put them there, the folders, what is unplaced
  unpack.assign {url, tab, items, season?, episode?, episode_last?, folder?, excluded?, in_order?} — placed by hand (a drag,
      «Не брать»); `in_order`: the items in the order given take the episodes from `episode` on, one each (files dropped on
      a season)
  unpack.unassign {url, tab, items} — back to what the rules give
  unpack.folder.set {url, kind: voice | subtitles, name, language?, subtitle_type?} / unpack.folder.delete {url, kind, name}
Events out:
  unpack.table.changed {url} — the rows of the page changed (`sources` reads its table again)
  unpack.editor.changed {url} — the editor of the page changed (a window shows it again)
  unpack.read.progress {url, release, done, total} — the files of a version read so far, while they are read; the editor
      gives the same as `reading` of a release, for a window opened in the middle of a read
Events in: torrents.release.changed (a release with its files: the files not to download are told to `torrents` by the
  numbers the preset gives the names of the files and the seasons and episodes the series exclude; a ready one is read),
  rules.profile.changed (a profile of the editor changed), series.updated (the episodes a series excludes changed).
"""
from __future__ import annotations

import json
import os

from core import BaseModule
from core.contracts.torrent import (RELEASE_CHANGED_TOPIC, EditorTab, FileKind, ReleaseState, ReleaseView, kind_of)
from core.db import Database
from core.envelope import Envelope
from core.ffprobe import layout_of
from core.ignored import parse_memory

from .layout import Inputs, Place, _places, compose, item_of, lines
from .repository import UnpackRepository

FILES_PROFILE_KEY = "unpack.files_profile_id"
FILES_PROFILE_NAME = "Торрент: файлы"
TABLE_CHANGED_TOPIC = "unpack.table.changed"
EDITOR_CHANGED_TOPIC = "unpack.editor.changed"
READ_PROGRESS_TOPIC = "unpack.read.progress"
READ = {FileKind.VIDEO, FileKind.AUDIO, FileKind.SUBTITLES}

# The rules the preset of the tab «Файлы» begins with: the forms of the numbers in the names of the releases of the corpus
# (DESIGN.md); the owner changes them in the editor like any rules.
_NUMBER, _TEXT = {"type": "number"}, (lambda value: {"type": "text", "value": value})
PRESET_RULES = [
    {"name": "не дорожка и не видео", "actions": [{"action_type": "exclude", "action_pattern": "[]"}],
     "conditions": [[_TEXT("K:другое")], [_TEXT("K:шрифт")]]},
    {"name": "сезон каталогом", "actions": [("extract_season", [_TEXT("Season "), _NUMBER])], "conditions": []},
    {"name": "S01E05", "actions": [("extract_season", [_TEXT("S"), _NUMBER, _TEXT("E")]),
                                   ("extract_single", [_TEXT("E"), _NUMBER])], "conditions": []},
    {"name": "серия после тире", "actions": [("extract_single", [_TEXT(" - "), _NUMBER])], "conditions": []},
    {"name": "серия в скобках", "actions": [("extract_single", [_TEXT("["), _NUMBER, _TEXT("]")])], "conditions": []},
]


class UnpackModule(BaseModule):
    name = "unpack"

    def __init__(self, bus, db: Database) -> None:
        self.repo = UnpackRepository(db)
        self._files_profile: int | None = None
        self._skips: dict[tuple[str, str], frozenset[str]] = {}  # the files not to download told to torrents last
        self._reading: dict[tuple[str, str], tuple[int, int]] = {}  # (url, release) → (files read, files to read)
        super().__init__(bus)

    def register(self) -> None:
        self.handle("unpack.table.get", self.on_table_get)
        self.handle("unpack.editor.get", self.on_editor_get)
        self.handle("unpack.assign", self.on_assign)
        self.handle("unpack.unassign", self.on_unassign)
        self.handle("unpack.folder.set", self.on_folder_set)
        self.handle("unpack.folder.delete", self.on_folder_delete)
        self.handle(RELEASE_CHANGED_TOPIC, self.on_release_changed)
        self.handle("rules.profile.changed", self.on_profile_changed)
        self.handle("series.updated", self.on_series_updated)

    async def on_start(self) -> None:
        try:
            self._files_profile = await self._preset()
        except Exception:  # noqa: BLE001 - without the preset the files are placed only by hand until the next start
            self.log.exception("the preset of the torrent editor was not made")

    async def _preset(self) -> int:
        stored = (await self.request("settings.value.get", {"key": FILES_PROFILE_KEY})).get("value")
        profiles = {p["id"] for p in await self.request("rules.profiles.list", {"kind": "torrent"})}
        if stored and int(stored) in profiles:
            return int(stored)
        made = (await self.request("rules.profiles.create", {"name": FILES_PROFILE_NAME, "kind": "torrent"}))["id"]
        for rule in PRESET_RULES:
            actions = [a if isinstance(a, dict) else {"action_type": a[0], "action_pattern": json.dumps(a[1], ensure_ascii=False)}
                       for a in rule["actions"]]
            conditions = [{"condition_type": "contains", "logical_operator": "OR" if i < len(rule["conditions"]) - 1 else "AND",
                           "pattern": json.dumps(blocks, ensure_ascii=False)} for i, blocks in enumerate(rule["conditions"])]
            await self.request("rules.rules.add", {"profile_id": made, "data": {
                "name": rule["name"], "continue_after_match": True, "action_pattern": json.dumps(actions, ensure_ascii=False),
                "conditions": conditions}})
        await self.request("settings.value.set", {"key": FILES_PROFILE_KEY, "value": str(made)})
        return made

    # --- the layout ---------------------------------------------------------------------------------------------------

    async def _releases(self, url: str) -> list[ReleaseView]:
        reply = await self.request("torrents.releases.get", {"url": url})
        return [ReleaseView.model_validate(each) for each in reply.get("releases") or []]

    async def _apply(self, profile_id: int | None, keyed: dict[str, str]) -> dict[str, dict]:
        if not profile_id or not keyed:
            return {}
        reply = await self.request("rules.apply", {"profile_id": profile_id,
                                                   "lines": [{"key": key, "text": text} for key, text in keyed.items()]})
        return reply.get("results") or {}

    async def _inputs(self, url: str, releases: list[ReleaseView] | None = None) -> Inputs:
        releases = releases if releases is not None else await self._releases(url)
        manual: dict[str, dict[str, dict]] = {tab.value: {} for tab in EditorTab}
        for row in await self.repo.assignments(url):
            manual[row["tab"]][row["item"]] = row
        streams = {r.release: await self.repo.streams(url, r.release) for r in releases}
        inputs = Inputs(releases=releases, streams=streams, results={}, manual=manual, folders=await self.repo.folders(url))
        profiles = await self.repo.profiles(url) or {}
        keyed = lines(inputs)
        inputs.results[EditorTab.FILES] = await self._apply(self._files_profile, keyed[EditorTab.FILES])
        keyed = lines(inputs)  # the tabs of tracks read the places the files got
        for tab, column in ((EditorTab.VOICES, "voices_profile_id"), (EditorTab.SUBTITLES, "subtitles_profile_id"),
                            (EditorTab.STREAMS, "streams_profile_id")):
            inputs.results[tab] = await self._apply(profiles.get(column), keyed[tab])
        return inputs

    async def on_table_get(self, env: Envelope) -> dict:
        url = env.payload["url"]
        releases = await self._releases(url)
        _view, rows = compose(await self._inputs(url, releases))
        names = [r.title for r in releases if r.title][:1]
        return {"names": names, "rows": [row.model_dump(mode="json") for row in rows]}

    async def on_editor_get(self, env: Envelope) -> dict:
        url = env.payload["url"]
        profiles = await self._tab_profiles(url)
        view, _rows = compose(await self._inputs(url))
        for release in view["releases"]:
            found = self._reading.get((url, release["release"]))
            release["reading"] = None if found is None else {"done": found[0], "total": found[1]}
        return {"url": url, "profiles": {"files": self._files_profile, **profiles}, "view": view}

    async def _tab_profiles(self, url: str) -> dict[str, int]:
        """The profiles of the tabs of the tracks of the source, made when its editor is first opened."""
        stored = await self.repo.profiles(url)
        if stored is None:
            made = {}
            for tab, word in (("voices", "озвучки"), ("subtitles", "субтитры"), ("streams", "контейнеры")):
                made[tab] = (await self.request("rules.profiles.create",
                                                {"name": f"Торрент: {url} · {word}", "kind": "torrent"}))["id"]
            await self.repo.save_profiles(url, made["voices"], made["subtitles"], made["streams"])
            return made
        return {"voices": stored["voices_profile_id"], "subtitles": stored["subtitles_profile_id"],
                "streams": stored["streams_profile_id"]}

    def _changed(self, url: str) -> None:
        self.publish_event(TABLE_CHANGED_TOPIC, {"url": url})
        self.publish_event(EDITOR_CHANGED_TOPIC, {"url": url})

    # --- what the user places -------------------------------------------------------------------------------------

    async def on_assign(self, env: Envelope) -> None:
        p = env.payload
        await self.repo.assign(p["url"], p["tab"], list(p["items"]), season=p.get("season"), episode=p.get("episode"),
                               episode_last=p.get("episode_last"), folder=p.get("folder"), excluded=bool(p.get("excluded")),
                               in_order=bool(p.get("in_order")))
        self._changed(p["url"])

    async def on_unassign(self, env: Envelope) -> None:
        await self.repo.unassign(env.payload["url"], env.payload["tab"], list(env.payload["items"]))
        self._changed(env.payload["url"])

    async def on_folder_set(self, env: Envelope) -> None:
        p = env.payload
        await self.repo.set_folder(p["url"], p["kind"], p["name"], p.get("language") or "", p.get("subtitle_type") or "")
        self._changed(p["url"])

    async def on_folder_delete(self, env: Envelope) -> None:
        await self.repo.delete_folder(env.payload["url"], env.payload["kind"], env.payload["name"])
        self._changed(env.payload["url"])

    # --- what torrents and rules tell ------------------------------------------------------------------------------

    async def on_release_changed(self, env: Envelope) -> None:
        release = ReleaseView.model_validate(env.payload)
        if release.files and release.state in (ReleaseState.DOWNLOADING, ReleaseState.UPDATING, ReleaseState.METADATA):
            await self._tell_skips(release)
        if release.state == ReleaseState.READY and release.folder:
            await self._read(release)
        self._changed(release.url)

    async def _read(self, release: ReleaseView) -> None:
        """Every video, sound and subtitles file of the version, read once per version and size."""
        known = await self.repo.streams(release.url, release.release)
        todo = [f for f in release.files if kind_of(f.path) in READ and f.priority != 0 and not (
            (stored := known.get(f.path)) and stored["infohash"] == release.infohash and stored["size"] == f.size)]
        key = (release.url, release.release)
        try:
            for done, f in enumerate(todo):
                self._progress(key, done, len(todo))
                layout, said = await layout_of(os.path.join(release.folder, f.path))
                await self.repo.save_streams(release.url, release.release, f.path, release.infohash or "", f.size, layout,
                                             None if layout is not None else said)
        finally:
            if todo:
                self._progress(key, len(todo), len(todo))
            self._reading.pop(key, None)
        await self.repo.drop_streams(release.url, release.release, {f.path for f in release.files})

    def _progress(self, key: tuple[str, str], done: int, total: int) -> None:
        self._reading[key] = (done, total)
        self.publish_event(READ_PROGRESS_TOPIC, {"url": key[0], "release": key[1], "done": done, "total": total})

    async def _tell_skips(self, release: ReleaseView) -> None:
        """The files whose episode every series with the page excludes are not downloaded."""
        inputs = await self._inputs(release.url, [release])
        memory = await self._excluded_of(release.url)
        if memory is None:
            return
        places = _places(inputs)
        skip = frozenset(f.path for f in release.files
                         if _skipped(places.get(item_of(release.release, f.path)), memory))
        if self._skips.get((release.url, release.release)) == skip:
            return
        self._skips[(release.url, release.release)] = skip
        self.send_command("torrents.release.priorities", {"url": release.url, "release": release.release,
                                                          "skip": sorted(skip)})

    async def _excluded_of(self, url: str) -> list[dict[int, set[int]]] | None:
        """What every series with the page excludes ({season: {episodes}}, a whole season as all its episodes asked);
        None when the series could not be asked."""
        try:
            series = await self.request("catalog.series.list", {})
        except Exception:  # noqa: BLE001 - with no list nothing is held back: every file is downloaded
            self.log.exception("the series of %s were not asked; nothing is held back", url)
            return None
        memories = []
        for one in series:
            pages = set()
            for source in one.get("sources") or []:
                resolved = await self.request("sources.site.resolve", {"url": source["url"]})
                pages.add(resolved.get("canonical_url") or source["url"])
            if url not in pages:
                continue
            seasons = one.get("ignored_seasons") or []
            seasons = json.loads(seasons) if isinstance(seasons, str) else seasons
            episodes = one.get("ignored_episodes") or {}
            memory = parse_memory(json.loads(episodes) if isinstance(episodes, str) else episodes)
            memories.append({"seasons": {int(s) for s in seasons}, "episodes": memory})
        return memories

    async def on_profile_changed(self, env: Envelope) -> None:
        profile_id = env.payload.get("profile_id")
        if profile_id == self._files_profile:
            for url in await self._pages():
                self._changed(url)
            return
        for url in await self._pages():
            stored = await self.repo.profiles(url) or {}
            if profile_id in (stored.get("voices_profile_id"), stored.get("subtitles_profile_id"),
                              stored.get("streams_profile_id")):
                self._changed(url)

    async def _pages(self) -> list[str]:
        reply = await self.request("catalog.series.list", {})
        pages = set()
        for one in reply:
            for source in one.get("sources") or []:
                if source.get("source_type") == "torrent":
                    resolved = await self.request("sources.site.resolve", {"url": source["url"]})
                    pages.add(resolved.get("canonical_url") or source["url"])
        return sorted(pages)

    async def on_series_updated(self, env: Envelope) -> None:
        if not {"ignored_seasons", "ignored_episodes"} & set(env.payload or {}):
            return
        for url in await self._pages():
            for release in await self._releases(url):
                if release.files and release.state != ReleaseState.ARCHIVED:
                    await self._tell_skips(release)


def _skipped(where, memories: list[dict]) -> bool:
    """A file is not downloaded when it is placed and every series with the page excludes all its episodes."""
    if not memories or not isinstance(where, Place):
        return False
    episodes = set(range(where.episode, (where.last or where.episode) + 1))

    def excluded(memory: dict) -> bool:
        return where.season in memory["seasons"] or episodes <= memory["episodes"].get(where.season, set())
    return all(excluded(memory) for memory in memories)

