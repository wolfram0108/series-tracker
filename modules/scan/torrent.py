"""The scan of a torrent series: the releases a site lists against the torrents in the client, the replacements to make, and
the journal of a task that carries them out.

Each step of a task is idempotent and its result is written as it comes, so a restart goes on from the journal. The
orchestration around it (the lock of the series, the status, the resume after a restart) stays in `module.py`.
"""
from __future__ import annotations

from core import BusRequestError, ids
from core.reasons import reason
from core.contracts import SeriesSource

from .errors import ScanAborted, ScanError
from .repository import ScanRepository

# How the releases of a tracker replace the active ones. fixed: a new release replaces the active one with the same
# episodes; rolling: with a single active release that one is replaced, with several nothing is (it is not known which
# one is out of date).
REPLACE_MODE = {"astar": "fixed"}
DEFAULT_REPLACE_MODE = "rolling"


class TorrentScan:
    def __init__(self, repo: ScanRepository, request, log, aborted: set[int]) -> None:
        self._repo, self._request, self._log = repo, request, log
        self._aborted = aborted  # series deleted during a scan

    async def scan(self, series: dict, source: SeriesSource, force: bool, prepare: bool = False) -> dict:
        series_id = series["id"]
        # A renamed file that is gone from the disk gets a recheck task (the client restores it); the old system did this
        # on every scan, the verification at the opening of the window did not heal a file lost between scans.
        try:
            await self._request("torrents.fs.verify", {"series_id": series_id})
            # Torrents that are active, not complete and without a task go back into the pipeline. A preparation skips it:
            # it would lift the pause before the episodes are chosen.
            if not prepare:
                await self._request("torrents.drive_incomplete", {"series_id": series_id})
        except BusRequestError as exc:
            self._log.warning("сверка файлов перед сканом пропущена: %s", exc)
        if force:
            self._log.warning("force_replace: все активные торренты сериала %d будут заменены", series_id)
            await self._request("torrents.db.deactivate_all", {"series_id": series_id})
        active = await self._request("torrents.db.active", {"series_id": series_id})

        parsed = await self._request("sources.parse", {"url": source.url})
        if series_id in self._aborted:
            raise ScanAborted(reason("series_deleted_in_scan", series_id=series_id))
        plan_items = self.plan(series, parsed, active)
        if not plan_items:
            self._log.info("сериал %d: новых раздач нет", series_id)
            return {"source_type": "torrent", "tasks_created": 0, "changed": False}

        task_id = await self._repo.create_task(series_id, plan_items)
        return await self.execute(task_id, series, plan_items, {}, prepare=prepare)

    def plan(self, series: dict, parsed: dict, active: list[dict]) -> list[dict]:
        """The facts of the parser and the active releases make the plan of replacements."""
        releases = []
        for r in parsed.get("releases", []):
            link_for_id = r.get("link") or r.get("magnet")
            if not link_for_id:
                continue
            releases.append({**r, "torrent_id": ids.torrent_id(link_for_id, r.get("date_marker"))})

        if series.get("quality"):
            wanted = {q.strip() for q in series["quality"].split(";") if q.strip()}
            releases = [r for r in releases if r.get("quality") in wanted]

        mode = REPLACE_MODE.get(parsed.get("service", ""), DEFAULT_REPLACE_MODE)
        active_ids = {t["torrent_id"] for t in active}
        plan = []
        for rel in releases:
            if rel["torrent_id"] in active_ids:
                continue  # the date did not change: the release is ours already
            old = None
            if mode == "fixed":
                old = next((t for t in active if t.get("episodes") == rel.get("episodes")), None)
            elif mode == "rolling" and len(active) == 1:
                old = active[0]
            plan.append({
                "site_torrent": {
                    "torrent_id": rel["torrent_id"],
                    "link": rel.get("link"),
                    "magnet": rel.get("magnet"),
                    "date_time": rel.get("date_marker"),
                    "quality": rel.get("quality"),
                    "episodes": rel.get("episodes"),
                },
                "old": ({"torrent_id": old["torrent_id"],
                         "qb_hash": old.get("qb_hash"),
                         "db_id": old.get("id")} if old else None),
            })
        return plan

    async def execute(self, task_id: int, series: dict, plan_items: list[dict], results: dict,
                      prepare: bool = False) -> dict:
        """Carry out the journal: each step is idempotent and its result is written as it comes, so after a failure the
        task goes on from the same place.

        `prepare`: the release is added and split into its files but does not enter the pipeline; the next scan starts
        the download."""
        series_id = series["id"]
        try:
            for idx, item in enumerate(plan_items):
                if series_id in self._aborted:
                    raise ScanAborted(reason("series_deleted_in_scan", series_id=series_id))
                if str(idx) in results:
                    continue  # done before the failure
                st = item["site_torrent"]
                add_payload: dict = {"save_path": series["save_path"], "paused": True}
                if st.get("link"):
                    file_reply = await self._request("sources.torrent_file.get",
                                                     {"url": st["link"], "torrent_id": st["torrent_id"]})
                    add_payload["content_b64"] = file_reply["content_b64"]
                else:
                    add_payload["magnet"] = st["magnet"]
                added = await self._request("torrents.add", add_payload)
                results[str(idx)] = {"hash": added["hash"], "link_type": added["link_type"]}
                await self._repo.update_results(task_id, results)

            if not results:
                raise ScanError(reason("torrent_none_added"))

            created = 0
            for idx_str, res in results.items():
                item = plan_items[int(idx_str)]
                reg = await self._request("torrents.register", {
                    "series_id": series_id,
                    "torrent": item["site_torrent"],
                    "qb_hash": res["hash"],
                    "link_type": res["link_type"],
                    "replaces": item["old"],
                    "start_pipeline": not prepare})
                # existed: the same infohash (a re-upload), nothing is created and it is not a change.
                if not (reg or {}).get("existed"):
                    created += 1

            await self._repo.delete_task(task_id)
            return {"source_type": "torrent", "tasks_created": created, "changed": created > 0}
        except ScanAborted:
            raise
        except Exception as exc:
            await self._repo.set_error(task_id, str(exc))
            raise
