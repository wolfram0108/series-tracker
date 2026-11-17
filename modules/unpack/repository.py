"""The SQL of the module unpack: tables unpack_streams, unpack_folders, unpack_assignments and unpack_sources."""
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
        rows = await self._db.fetch_all("SELECT * FROM unpack_assignments WHERE url=?", (url,))
        return self._db.coerce_bools(rows, ("excluded",))

    async def assign(self, url: str, tab: str, items: list[str], *, season: int | None = None, episode: int | None = None,
                     episode_last: int | None = None, folder: str | None = None, excluded: bool = False,
                     in_order: bool = False) -> None:
        """`in_order`: the items in the order given take the episodes from `episode` on, one each."""
        await self._db.execute_many([
            ("INSERT OR REPLACE INTO unpack_assignments (url, tab, item, season, episode, episode_last, folder, excluded) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (url, tab, item, season, episode + number if in_order and episode is not None else episode,
              None if in_order else episode_last, folder, int(excluded)))
            for number, item in enumerate(items)])

    async def unassign(self, url: str, tab: str, items: list[str]) -> None:
        await self._db.execute_many([("DELETE FROM unpack_assignments WHERE url=? AND tab=? AND item=?", (url, tab, item))
                                     for item in items])

    async def folders(self, url: str) -> list[dict]:
        return await self._db.fetch_all("SELECT kind, name, language, subtitle_type FROM unpack_folders WHERE url=? "
                                        "ORDER BY kind, name", (url,))

    async def set_folder(self, url: str, kind: str, name: str, language: str, subtitle_type: str) -> None:
        await self._db.execute("INSERT OR REPLACE INTO unpack_folders (url, kind, name, language, subtitle_type) "
                               "VALUES (?, ?, ?, ?, ?)", (url, kind, name, language, subtitle_type))

    async def delete_folder(self, url: str, kind: str, name: str) -> None:
        tabs = ("voices", "streams") if kind == "voice" else ("subtitles", "streams")
        await self._db.execute_many(
            [("DELETE FROM unpack_folders WHERE url=? AND kind=? AND name=?", (url, kind, name))]
            + [("DELETE FROM unpack_assignments WHERE url=? AND tab=? AND folder=?", (url, tab, name)) for tab in tabs])

    # --- the profiles of the tabs of a source -----------------------------------------------------------------------

    async def profiles(self, url: str) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM unpack_sources WHERE url=?", (url,))

    async def save_profiles(self, url: str, voices: int, subtitles: int, streams: int) -> None:
        await self._db.execute("INSERT OR REPLACE INTO unpack_sources (url, voices_profile_id, subtitles_profile_id, "
                               "streams_profile_id) VALUES (?, ?, ?, ?)", (url, voices, subtitles, streams))

    async def forget(self, url: str) -> None:
        await self._db.execute_many([("DELETE FROM unpack_streams WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_folders WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_assignments WHERE url=?", (url,)),
                                     ("DELETE FROM unpack_sources WHERE url=?", (url,))])
