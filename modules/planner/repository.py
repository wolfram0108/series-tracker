"""The store of the planner: the asset of every series (its containers as the table of the asset), the attempts that
failed, the commands «Удалить» that wait, and the syncs under way (taken up again after a stop)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database


def _key(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PlannerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def assets(self, series_id: int) -> list[dict]:
        return [json.loads(row["data"]) for row in await self._db.fetch_all(
            "SELECT data FROM plan_assets WHERE series_id=? ORDER BY name", (series_id,))]

    async def series_with_assets(self) -> list[int]:
        return [row["series_id"] for row in await self._db.fetch_all("SELECT DISTINCT series_id FROM plan_assets")]

    async def save(self, series_id: int, assets: list[dict], failures: list, commands: list) -> None:
        """The table of the asset, the failures and the commands of a series, in one transaction."""
        statements = [("DELETE FROM plan_assets WHERE series_id=?", (series_id,)),
                      ("DELETE FROM plan_failures WHERE series_id=?", (series_id,)),
                      ("DELETE FROM plan_commands WHERE series_id=?", (series_id,))]
        statements += [("INSERT INTO plan_assets (series_id, name, data) VALUES (?, ?, ?)",
                        (series_id, each["name"], json.dumps(each, ensure_ascii=False))) for each in assets]
        statements += [("INSERT INTO plan_failures (series_id, key) VALUES (?, ?)", (series_id, _key(each)))
                       for each in failures]
        statements += [("INSERT INTO plan_commands (series_id, command) VALUES (?, ?)", (series_id, _key(each)))
                       for each in commands]
        await self._db.execute_many(statements)

    async def episodes(self) -> dict[int, set[tuple[int, int]]]:
        """The episodes every series holds a file of that the media server sees (not switched off)."""
        found: dict[int, set[tuple[int, int]]] = {}
        for row in await self._db.fetch_all("SELECT series_id, data FROM plan_assets"):
            data = json.loads(row["data"])
            if not data.get("off"):
                season, episode = data["video"]["episode"][:2]
                found.setdefault(row["series_id"], set()).add((season, episode))
        return found

    async def failures(self, series_id: int) -> list:
        return [json.loads(row["key"]) for row in await self._db.fetch_all(
            "SELECT key FROM plan_failures WHERE series_id=? ORDER BY key", (series_id,))]

    async def forget_failures(self, series_id: int) -> int:
        return await self._db.execute("DELETE FROM plan_failures WHERE series_id=?", (series_id,))

    async def commands(self, series_id: int) -> list:
        return [json.loads(row["command"]) for row in await self._db.fetch_all(
            "SELECT command FROM plan_commands WHERE series_id=? ORDER BY command", (series_id,))]

    async def add_command(self, series_id: int, command: list) -> None:
        await self._db.execute("INSERT OR IGNORE INTO plan_commands (series_id, command) VALUES (?, ?)",
                               (series_id, _key(command)))

    async def drop_command(self, series_id: int, command: list) -> int:
        return await self._db.execute("DELETE FROM plan_commands WHERE series_id=? AND command=?",
                                      (series_id, _key(command)))

    async def begin_run(self, series_id: int, download: bool) -> None:
        await self._db.execute(
            "INSERT INTO plan_runs (series_id, download, started_at) VALUES (?, ?, ?) "
            "ON CONFLICT (series_id) DO UPDATE SET download=excluded.download",
            (series_id, int(download), datetime.now(timezone.utc).isoformat()))

    async def end_run(self, series_id: int) -> None:
        await self._db.execute("DELETE FROM plan_runs WHERE series_id=?", (series_id,))

    async def running(self, series_id: int) -> bool:
        return await self._db.fetch_one("SELECT 1 FROM plan_runs WHERE series_id=?", (series_id,)) is not None

    async def runs(self) -> list[dict]:
        return await self._db.fetch_all("SELECT series_id, download FROM plan_runs ORDER BY series_id")

    async def purge(self, series_id: int) -> None:
        await self._db.execute_many([(f"DELETE FROM {table} WHERE series_id=?", (series_id,))
                                     for table in ("plan_assets", "plan_failures", "plan_commands", "plan_runs")])
