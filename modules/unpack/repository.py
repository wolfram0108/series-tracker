"""The SQL of the module unpack: tables unpack_streams, unpack_folders, unpack_assignments, unpack_hidden, unpack_removed and
unpack_sources."""
from __future__ import annotations

import json

from core.db import Database


class UnpackRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- what a file holds ---------------------------------------------------------------------------------------

    async def streams(self, url: str, release: str) -> dict[str, dict]:
        rows = await self._db.fetch_all("SELECT * FROM unpack_streams WHERE url=? AND release=?", (url, release))
        return {row["path"]: {**row, "streams": json.loads(row["streams"]), "attachments": json.loads(row["attachments"])}
                for row in rows}

    async def save_streams(self, url: str, release: str, path: str, infohash: str, size: int, layout: dict | None,
                           problem: str | None) -> None:
        streams = [each for each in (layout or {}).get("streams", []) if each["kind"] != "attachment"]
        attachments = [each for each in (layout or {}).get("streams", []) if each["kind"] == "attachment"]
        await self._db.execute(
            "INSERT OR REPLACE INTO unpack_streams (url, release, path, infohash, size, streams, attachments, chapters, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (url, release, path, infohash, size, json.dumps(streams, ensure_ascii=False),
             json.dumps(attachments, ensure_ascii=False), (layout or {}).get("chapters", 0), problem))

    async def drop_streams(self, url: str, release: str, keep: set[str]) -> None:
        for row in await self._db.fetch_all("SELECT path FROM unpack_streams WHERE url=? AND release=?", (url, release)):
            if row["path"] not in keep:
                await self._db.execute("DELETE FROM unpack_streams WHERE url=? AND release=? AND path=?",
                                       (url, release, row["path"]))

    # --- what the user placed by hand -----------------------------------------------------------------------------

    async def assignments(self, url: str) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM unpack_assignments WHERE url=?", (url,))

    async def assign(self, url: str, tab: str, items: list[str], *, season: int | None = None, episode: int | None = None,
                     episode_last: int | None = None, folder: str | None = None, in_order: bool = False) -> None:
        """`in_order`: the items in the order given take the episodes from `episode` on, one each."""
        await self._db.execute_many([
            ("INSERT OR REPLACE INTO unpack_assignments (url, tab, item, season, episode, episode_last, folder) "
             "VALUES (?, ?, ?, ?, ?, ?, ?)",
             (url, tab, item, season, episode + number if in_order and episode is not None else episode,
              None if in_order else episode_last, folder))
            for number, item in enumerate(items)]
            # A track placed by hand is no longer one the user took out of its folder.
            + [("DELETE FROM unpack_removed WHERE url=? AND tab=? AND item=?", (url, tab, item)) for item in items])

    async def remove(self, url: str, tab: str, items: list[str]) -> None:
        """«−»: the tracks are in no folder — red — whatever the files and the rules say; a hand's placement goes too."""
        await self._db.execute_many(
            [("DELETE FROM unpack_assignments WHERE url=? AND tab=? AND item=?", (url, tab, item)) for item in items]
            + [("INSERT OR IGNORE INTO unpack_removed (url, tab, item) VALUES (?, ?, ?)", (url, tab, item)) for item in items])

    async def removed(self, url: str) -> list[dict]:
        return await self._db.fetch_all("SELECT tab, item FROM unpack_removed WHERE url=?", (url,))

    async def unassign(self, url: str, tab: str, items: list[str]) -> None:
        await self._db.execute_many([("DELETE FROM unpack_assignments WHERE url=? AND tab=? AND item=?", (url, tab, item))
                                     for item in items])

    async def folders(self, url: str) -> list[dict]:
        return await self._db.fetch_all("SELECT kind, name, language, subtitle_type FROM unpack_folders WHERE url=? "
                                        "ORDER BY kind, name", (url,))

    async def set_folder(self, url: str, kind: str, name: str, language: str, subtitle_type: str) -> None:
        await self._db.execute("INSERT OR REPLACE INTO unpack_folders (url, kind, name, language, subtitle_type) "
                               "VALUES (?, ?, ?, ?, ?)", (url, kind, name, language, subtitle_type))

    async def delete_folder(self, url: str, kind: str, name: str, items: dict[str, list[str]], automatic: bool) -> None:
        """The folder goes: its settings, the hand's placements in it (`items`: tab → items, as the editor names them), and
        one of the automatic layout (`automatic`) is not made by it again until «Авто»."""
        await self._db.execute_many(
            [("DELETE FROM unpack_folders WHERE url=? AND kind=? AND name=?", (url, kind, name))]
            + ([("INSERT OR IGNORE INTO unpack_hidden (url, kind, name) VALUES (?, ?, ?)", (url, kind, name))] if automatic else [])
            + [("DELETE FROM unpack_assignments WHERE url=? AND tab=? AND item=? AND folder=?", (url, tab, item, name))
               for tab, listed in items.items() for item in listed])

    async def hidden(self, url: str) -> list[dict]:
        """The folders of the automatic layout the user deleted."""
        return await self._db.fetch_all("SELECT kind, name FROM unpack_hidden WHERE url=? ORDER BY kind, name", (url,))

    async def unhide(self, url: str, kinds: list[str], name: str | None = None) -> None:
        """«Авто»: the automatic layout makes the folders of these kinds again (one, with `name`), and with all of a kind
        places again the tracks of its tabs the user took out of their folders."""
        tabs = {"voice": ("voices", "voice_streams"), "subtitles": ("subtitles", "subtitle_streams")}
        await self._db.execute_many([("DELETE FROM unpack_hidden WHERE url=? AND kind=?" + (" AND name=?" if name else ""),
                                      (url, kind, name) if name else (url, kind)) for kind in kinds]
                                    + ([] if name else [("DELETE FROM unpack_removed WHERE url=? AND tab=?", (url, tab))
                                                        for kind in kinds for tab in tabs[kind]]))

    # --- the profiles of the tabs of a source -----------------------------------------------------------------------

    async def profiles(self, url: str) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM unpack_sources WHERE url=?", (url,))

    async def save_profiles(self, url: str, profiles: dict[str, int]) -> None:
        """`profiles`: tab → profile id, for the five tabs of the editor."""
        await self._db.execute("INSERT OR REPLACE INTO unpack_sources (url, files_profile_id, voices_profile_id, "
                               "subtitles_profile_id, voice_streams_profile_id, subtitle_streams_profile_id) VALUES (?, ?, ?, ?, ?, ?)",
                               (url, profiles["files"], profiles["voices"], profiles["subtitles"], profiles["voice_streams"],
                                profiles["subtitle_streams"]))

    async def pages(self) -> list[str]:
        """Every page the module keeps something of."""
        rows = await self._db.fetch_all("SELECT url FROM unpack_sources UNION SELECT url FROM unpack_streams "
                                        "UNION SELECT url FROM unpack_folders UNION SELECT url FROM unpack_assignments "
                                        "UNION SELECT url FROM unpack_hidden UNION SELECT url FROM unpack_removed")
        return sorted(row["url"] for row in rows)

    async def forget(self, url: str) -> None:
        await self._db.execute_many([("DELETE FROM unpack_streams WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_folders WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_assignments WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_hidden WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_removed WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_sources WHERE url=?", (url,))])
