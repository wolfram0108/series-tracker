"""The store of the planner: the asset of every series (its containers as the table of the asset), the attempts that
failed, the commands «Удалить» that wait, and the syncs under way (taken up again after a stop)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from core.contracts import PackCard
from core.db import Database
from core.plan.files import container_of
from core.plan.tables import Container


def _key(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PlannerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def assets(self, series_id: int) -> list[dict]:
        """The rows of the asset of a series; read on the thread of the query (hundreds of files with their tracks)."""
        return await self._db.transaction(lambda conn: [json.loads(row[0]) for row in conn.execute(
            "SELECT data FROM plan_assets WHERE series_id=? ORDER BY name", (series_id,))])

    async def containers(self, series_id: int) -> list[Container]:
        """The files of a series as the planner reads them, made on the thread of the query."""
        return await self._db.transaction(lambda conn: [container_of(json.loads(row[0])) for row in conn.execute(
            "SELECT data FROM plan_assets WHERE series_id=? ORDER BY name", (series_id,))])

    async def series_with_assets(self) -> list[int]:
        return [row["series_id"] for row in await self._db.fetch_all("SELECT DISTINCT series_id FROM plan_assets")]

    async def save(self, series_id: int, assets: list[dict], failures: list, commands: list) -> None:
        """The table of the asset, the failures and the commands of a series, in one transaction. The rows are written
        out on the thread of the query: hundreds of files with their tracks."""
        def write(conn) -> None:
            conn.execute("DELETE FROM plan_assets WHERE series_id=?", (series_id,))
            conn.execute("DELETE FROM plan_failures WHERE series_id=?", (series_id,))
            conn.execute("DELETE FROM plan_commands WHERE series_id=?", (series_id,))
            conn.executemany("INSERT INTO plan_assets (series_id, name, data) VALUES (?, ?, ?)",
                             [(series_id, each["name"], json.dumps(each, ensure_ascii=False)) for each in assets])
            conn.executemany("INSERT INTO plan_failures (series_id, key) VALUES (?, ?)",
                             [(series_id, _key(each)) for each in failures])
            conn.executemany("INSERT INTO plan_commands (series_id, command) VALUES (?, ?)",
                             [(series_id, _key(each)) for each in commands])
        await self._db.transaction(write)

    async def episodes(self, series_id: int | None = None) -> dict[int, set[tuple[int, int]]]:
        """The episodes every series (or the one given) holds a file of that the media server sees (not switched off);
        read on the thread of the query."""
        def read(conn) -> dict[int, set[tuple[int, int]]]:
            found: dict[int, set[tuple[int, int]]] = {}
            rows = conn.execute("SELECT series_id, data FROM plan_assets" + (" WHERE series_id=?" if series_id is not None
                                                                              else ""),
                                () if series_id is None else (series_id,))
            for held, data in rows:
                data = json.loads(data)
                if not data.get("off"):
                    season, episode = data["video"]["episode"][:2]
                    found.setdefault(held, set()).add((season, episode))
            return found
        return await self._db.transaction(read)

    async def failures(self, series_id: int) -> list:
        return [json.loads(row["key"]) for row in await self._db.fetch_all(
            "SELECT key FROM plan_failures WHERE series_id=? ORDER BY key", (series_id,))]

    async def forget_failures(self, series_id: int, failures: list | None = None) -> int:
        """The failed attempts of the series forgotten: all of them, or those given (as `failures` gives them)."""
        if failures is None:
            return await self._db.execute("DELETE FROM plan_failures WHERE series_id=?", (series_id,))
        found = 0
        for each in failures:
            found += await self._db.execute("DELETE FROM plan_failures WHERE series_id=? AND key=?", (series_id, _key(each)))
        return found

    async def commands(self, series_id: int) -> list:
        return [json.loads(row["command"]) for row in await self._db.fetch_all(
            "SELECT command FROM plan_commands WHERE series_id=? ORDER BY command", (series_id,))]

    async def add_command(self, series_id: int, command: list) -> None:
        await self._db.execute("INSERT OR IGNORE INTO plan_commands (series_id, command) VALUES (?, ?)",
                               (series_id, _key(command)))

    async def drop_command(self, series_id: int, command: list) -> int:
        return await self._db.execute("DELETE FROM plan_commands WHERE series_id=? AND command=?",
                                      (series_id, _key(command)))

    async def begin_run(self, series_id: int, download: bool, scope: dict | None = None) -> None:
        """The sync of the series is written down with what it covers (`scope`: none, the whole series), so that a restart
        takes it up as it was."""
        await self._db.execute(
            "INSERT INTO plan_runs (series_id, download, started_at, scope) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (series_id) DO UPDATE SET download=excluded.download, scope=excluded.scope",
            (series_id, int(download), datetime.now(timezone.utc).isoformat(), None if scope is None else _key(scope)))

    async def run_started(self, series_id: int) -> str:
        """When the sync under way began: the same after a stop, so its work is known again by it."""
        row = await self._db.fetch_one("SELECT started_at FROM plan_runs WHERE series_id=?", (series_id,))
        return row["started_at"] if row else ""

    async def end_run(self, series_id: int) -> None:
        await self._db.execute("DELETE FROM plan_runs WHERE series_id=?", (series_id,))

    async def running(self, series_id: int) -> bool:
        return await self._db.fetch_one("SELECT 1 FROM plan_runs WHERE series_id=?", (series_id,)) is not None

    async def runs(self) -> list[dict]:
        return [{**row, "scope": json.loads(row["scope"]) if row["scope"] else None} for row in await self._db.fetch_all(
            "SELECT series_id, download, scope FROM plan_runs ORDER BY series_id")]

    async def save_outcome(self, series_id: int, problems: list[dict]) -> None:
        """The outcome of the last sync: the work that failed for the moment; a sync that left none clears it."""
        if problems:
            await self._db.execute("INSERT INTO plan_outcomes (series_id, data) VALUES (?, ?) ON CONFLICT (series_id) "
                                   "DO UPDATE SET data=excluded.data", (series_id, json.dumps(problems, ensure_ascii=False)))
        else:
            await self._db.execute("DELETE FROM plan_outcomes WHERE series_id=?", (series_id,))

    async def outcome(self, series_id: int) -> list[dict]:
        row = await self._db.fetch_one("SELECT data FROM plan_outcomes WHERE series_id=?", (series_id,))
        return json.loads(row["data"]) if row else []

    async def purge(self, series_id: int) -> None:
        await self._db.execute_many([(f"DELETE FROM {table} WHERE series_id=?", (series_id,)) for table in
                                     ("plan_assets", "plan_failures", "plan_commands", "plan_runs", "plan_outcomes",
                                      "plan_standing")])

    async def standing(self) -> dict[int, tuple[dict[str, bool], str | None]]:
        """What every series stood at when its view was last counted (ready, waiting, error), and the print of the inputs
        it was counted from."""
        return {row["series_id"]: (json.loads(row["flags"]), row["inputs"])
                for row in await self._db.fetch_all("SELECT series_id, flags, inputs FROM plan_standing")}

    async def save_standing(self, series_id: int, flags: dict[str, bool], inputs: str | None) -> None:
        await self._db.execute("INSERT INTO plan_standing (series_id, flags, inputs) VALUES (?, ?, ?) ON CONFLICT "
                               "(series_id) DO UPDATE SET flags=excluded.flags, inputs=excluded.inputs",
                               (series_id, json.dumps(flags, sort_keys=True), inputs))

    async def own_print(self, series_id: int) -> str:
        """The print of what the module keeps of a series (the asset, the failures, the commands, the outcome of the last
        sync), as it is stored, in one transaction."""
        def read(conn) -> str:
            digest = hashlib.sha256()
            for query in ("SELECT name, data FROM plan_assets WHERE series_id=? ORDER BY name",
                          "SELECT key FROM plan_failures WHERE series_id=? ORDER BY key",
                          "SELECT command FROM plan_commands WHERE series_id=? ORDER BY command",
                          "SELECT data FROM plan_outcomes WHERE series_id=?"):
                digest.update(query.encode())
                for row in conn.execute(query, (series_id,)):
                    digest.update(json.dumps(list(row), ensure_ascii=False).encode())
            return digest.hexdigest()
        return await self._db.transaction(read)

    async def pack_cards(self) -> list[PackCard]:
        return [PackCard.model_validate(json.loads(row["data"]))
                for row in await self._db.fetch_all("SELECT data FROM pack_cards ORDER BY rowid")]

    async def keep_pack_cards(self, cards: list[PackCard]) -> None:
        if cards:
            await self._db.execute_many([(
                "INSERT INTO pack_cards (id, series_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET series_id = excluded.series_id, data = excluded.data",
                (card.id, card.series_id, card.model_dump_json())) for card in cards])

    async def drop_pack_cards(self, ids: list[str]) -> None:
        if ids:
            await self._db.execute_many([("DELETE FROM pack_cards WHERE id = ?", (each,)) for each in ids])
