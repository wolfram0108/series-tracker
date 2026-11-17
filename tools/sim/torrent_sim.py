"""The simulator of the tab «Торрент»: the whole application, as the service is assembled, on a database of its own, where any
release of the snapshots of torrents (data/raw/torrent_files__*.jsonl, tools/collectors/torrent_files.py) is taken as a source
of a series of its own and opened in the real tab — the same screens, the same layout of the module unpack, the same constructor.

What is outside the service is a model: the page of a tracker (anilibria's API, one release whose description is the name of the
.torrent), and qBittorrent (tests/qbit_model.py), which knows the release by the list of files of its snapshot and "downloads"
it as files of their true size that hold nothing (sparse: no room on the disk). A snapshot has the paths and sizes of the files,
not what is inside them, so the tracks inside the containers are not known: the tabs «Озв. конт.» and «Суб. конт.» show what a
release whose files cannot be read shows.

Usage: torrent_sim.py [--port 5100] [--root data/sim/torrent] [--snapshots <jsonl>]
As a service of its own (it outlives the shell; the kernel kills it, not the service, when memory is short):
  systemd-run --unit st-torrent-sim -p MemoryMax=700M -p OOMScoreAdjust=900 .venv/bin/python tools/sim/torrent_sim.py
  (from the folder of the repository; after a fix of the code: systemctl stop st-torrent-sim, then the same line)
The catalog of the snapshots is at /sim, the application at / (entered with the administrator of the service, ST_ADMIN_* of
.env; the cookie is not bound to HTTPS, the simulator is served on the local network over plain HTTP). The database, the releases taken and the state of the model of
qBittorrent are kept under --root, so a restart (after a fix) finds everything as it was. Prints `SIM_READY <url>` when served.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from assembly import Options, assemble  # noqa: E402
from core import Bus, Envelope, Runner  # noqa: E402
from core.db import Database  # noqa: E402
from qbit_model import QbitModel, _File, _Release, _Torrent, bdecode, bencode, magnet_of  # noqa: E402

PIECE = 16 * 1024 * 1024  # the pieces of a listed release are never read: a large piece keeps its table small
TRACKER = "anilibria.top"
PAGE = f"https://{TRACKER}/anime/releases/release/"
FOUND_MAX = 200


# --- the snapshots -----------------------------------------------------------------------------------------------------

class Snapshots:
    """The index of a file of snapshots: per line its offset, the name of the .torrent, the count and size of its files; a
    record itself is read from the file when asked (12 574 lists of files are not held in memory)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.index: list[dict] = []
        with open(path, "rb") as fh:
            offset = 0
            for line in fh:
                record = json.loads(line)
                files = record.get("files") or []
                self.index.append({"n": len(self.index), "offset": offset, "torrent": record.get("torrent") or "",
                                   "files": len(files), "size": sum(f.get("size") or 0 for f in files),
                                   "error": record.get("error")})
                offset += len(line)

    def record(self, n: int) -> dict:
        with open(self.path, "rb") as fh:
            fh.seek(self.index[n]["offset"])
            return json.loads(fh.readline())

    def find(self, text: str) -> tuple[int, list[dict]]:
        words = text.lower().split()
        found = [each for each in self.index if not each["error"] and each["files"]
                 and all(word in each["torrent"].lower() for word in words)]
        return len(found), found[:FOUND_MAX]


def listing_of(record: dict) -> tuple[str, list[tuple[str, int]], bool]:
    """(the name of the release, its files as the client names them, single file): a multi-file torrent names its files under
    its root folder; a single-file one names the file alone."""
    files = record["files"]
    root = record.get("root")
    if root:
        return root, [(f"{root}/{f['path']}", f["size"]) for f in files], False
    return files[0]["path"], [(files[0]["path"], files[0]["size"])], True


# --- qBittorrent -------------------------------------------------------------------------------------------------------

class ListingQbit(QbitModel):
    """The model of qBittorrent that also knows releases by their list of files alone: their files are made at their size
    and hold nothing, and the release is complete at once. The torrents it holds are kept in a file, so the application
    started again finds its client as it was."""

    def __init__(self, state_path: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state_path = state_path
        self.listed: set[str] = set()

    def list_release(self, key: str, name: str, files: list[tuple[str, int]], single: bool) -> str:
        """Somebody seeds the release of this list from now on; its infohash (the same for the same key on every run)."""
        total = sum(size for _, size in files)
        info: dict = {"name": name, "piece length": PIECE, "pieces": b"\0" * 20 * max(1, math.ceil(total / PIECE)),
                      "st-sim": key}
        if single:
            info["length"] = files[0][1]
        else:
            info["files"] = [{"length": size, "path": path[len(name) + 1:].split("/")} for path, size in files]
        h = hashlib.sha1(bencode(info)).hexdigest()
        with self._lock:
            self.swarm[h] = _Release(bencode({"info": info}), {}, False)
            self.listed.add(h)
            self._advance(h)
        return h

    def _attach_meta(self, t: _Torrent, meta: bytes) -> None:
        info = bdecode(meta)[0]["info"]
        if "files" in info:
            return super()._attach_meta(t, meta)
        t.name, t.piece = info["name"].decode(), info["piece length"]
        t.hashes = [info["pieces"][i:i + 20] for i in range(0, len(info["pieces"]), 20)]
        t.files = [_File(t.name, info["length"], 0)]

    def _advance(self, h: str) -> None:
        if h not in self.listed:
            return super()._advance(h)
        t, release = self.torrents.get(h), self.swarm.get(h)
        if t is None or release is None or t.stopped:
            return
        if not t.meta:
            self._attach_meta(t, release.meta)
        for f in t.wanted():
            where = Path(t.save_path) / f.path
            where.parent.mkdir(parents=True, exist_ok=True)
            with open(where, "ab"):
                pass
            os.truncate(where, f.size)  # sparse: the size of the file, no room on the disk
        t.have = set(range(len(t.hashes)))

    def _recheck(self, t: _Torrent) -> None:
        if t.hash not in self.listed:
            return super()._recheck(t)
        whole = all((p := Path(t.save_path) / f.path).exists() and p.stat().st_size == f.size for f in t.wanted())
        t.have = set(range(len(t.hashes))) if whole else set()

    async def _dispatch(self, request: Request) -> Response:
        answer = await super()._dispatch(request)
        with self._lock:
            self.save()
        return answer

    def save(self) -> None:
        state = {"categories": self.categories, "clock": self._clock,
                 "torrents": [{"hash": t.hash, "save_path": t.save_path, "category": t.category, "stopped": t.stopped,
                               "added_on": t.added_on, "priorities": [f.priority for f in t.files]}
                              for t in self.torrents.values()]}
        part = self.state_path + ".part"
        with open(part, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(part, self.state_path)

    def load(self) -> None:
        """The torrents of the last run, after the releases they are of were listed again."""
        if not os.path.exists(self.state_path):
            return
        with open(self.state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        self.categories, self._clock = state["categories"], state["clock"]
        for kept in state["torrents"]:
            t = _Torrent(kept["hash"], kept["save_path"], kept["category"], kept["stopped"], added_on=kept["added_on"])
            if kept["hash"] in self.swarm:
                self._attach_meta(t, self.swarm[kept["hash"]].meta)
                for f, priority in zip(t.files, kept["priorities"]):
                    f.priority = priority
                self._recheck(t)
            self.torrents[t.hash] = t


# --- the simulator -----------------------------------------------------------------------------------------------------

class _Server(uvicorn.Server):
    """A server that tells when it listens: `up` is set by its own startup, nobody looks at it by time."""

    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.up = asyncio.Event()

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        self.up.set()

    @property
    def port(self) -> int:
        return self.servers[0].sockets[0].getsockname()[1]


class Simulator:
    def __init__(self, root: str, snapshots: str) -> None:
        self.root = root
        for name in ("staging", "library", "cache"):
            os.makedirs(os.path.join(root, name), exist_ok=True)
        self.db_path = os.path.join(root, "app.db")
        self.taken_path = os.path.join(root, "taken.json")
        self.snapshots = Snapshots(snapshots)
        self.qbit = ListingQbit(os.path.join(root, "qbit.json"))
        self.pages: dict[str, dict] = {}  # alias → the answer of the tracker's API
        self.taken: dict[str, dict] = {}  # number of the snapshot → {alias, infohash, series_id}
        self._take = asyncio.Lock()

    def _migrate(self) -> None:
        """The schema of the database at the head of the migrations (a database of the last run is brought up to it)."""
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True, capture_output=True,
                       env={**os.environ, "ST_DB_URL": f"sqlite:///{self.db_path}"})

    def _tracker(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.strip("/")
        if request.url.host == TRACKER and path.startswith("api/v1/anime/releases/"):
            page = self.pages.get(path.removeprefix("api/v1/anime/releases/"))
            if page is not None:
                return httpx.Response(200, text=json.dumps(page, ensure_ascii=False))
        return httpx.Response(404, text="")

    def _list(self, n: int) -> tuple[str, str]:
        """The page and the release of snapshot `n` in the models: (alias, infohash)."""
        record = self.snapshots.record(n)
        name, files, single = listing_of(record)
        h = self.qbit.list_release(str(n), name, files, single)
        alias = f"sim-{n}"
        title = record["torrent"].removesuffix(".torrent")
        self.pages[alias] = {"name": {"main": title, "english": alias}, "is_ongoing": False, "episodes_total": None,
                             "torrents": [{"magnet": magnet_of(h, alias), "hash": h, "updated_at": "2026-11-16T11:46:00+00:00",
                                           "description": record["torrent"]}]}
        return alias, h

    async def start(self, port: int) -> None:
        self._migrate()
        if os.path.exists(self.taken_path):
            with open(self.taken_path, encoding="utf-8") as fh:
                self.taken = json.load(fh)
        for n in self.taken:
            self._list(int(n))
        self.qbit.load()
        qbit_server = _Server(self.qbit.app, "127.0.0.1", 0)
        qbit_task = asyncio.create_task(qbit_server.serve())
        await qbit_server.up.wait()
        self.bus = Bus()
        self.db = Database(self.db_path)
        self.gateway, self.modules = assemble(self.bus, self.db, Options(
            db_path=self.db_path, auth_required=True, cookie_secure=False,
            torrent_cache_dir=os.path.join(self.root, "cache"), qbit_url=f"http://127.0.0.1:{qbit_server.port}",
            qbit_username=self.qbit.username, qbit_password=self.qbit.password))
        self.runner = Runner(self.bus, self.modules)
        await self.runner.start()
        next(each for each in self.modules if each.name == "sources")._http = httpx.AsyncClient(
            transport=httpx.MockTransport(self._tracker))
        self.bus.publish(Envelope(topic="system.started", kind="event"))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.gateway.app), base_url="http://sim")
        # The folder of the releases is the simulator's own setting: written as the administrator, once per start.
        entered = await self.api.post("/api/login", json={"username": os.environ.get("ST_ADMIN_USER", ""),
                                                           "password": os.environ.get("ST_ADMIN_PASSWORD", "")})
        own = {"cookie": entered.headers.get("set-cookie", "").split(";")[0]}
        await self._call("POST", "/api/settings/torrent_staging", {"value": os.path.join(self.root, "staging")}, own)
        app = Starlette(routes=[Route("/sim", self.page), Route("/sim/api/find", self.find),
                                Route("/sim/api/take", self.take, methods=["POST"]), Mount("/", app=self.gateway.app)])
        server = _Server(app, "0.0.0.0", port)
        serving = asyncio.create_task(server.serve())
        await server.up.wait()
        print(f"SIM_READY http://0.0.0.0:{port}/sim", flush=True)
        await serving
        qbit_server.should_exit = True
        await qbit_task
        await self.runner.stop()

    async def _call(self, method: str, path: str, body=None, who: dict | None = None) -> dict:
        response = await self.api.request(method, path, json=body, headers=who)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else {}

    # --- the catalog ------------------------------------------------------------------------------------------------

    @staticmethod
    def _who(request: Request) -> dict:
        """The session of the user who asks: the catalog acts as him, through the gateway."""
        return {"cookie": request.headers.get("cookie", "")}

    async def _entered(self, request: Request) -> bool:
        return (await self.api.get("/api/me", headers=self._who(request))).status_code < 400

    async def page(self, request: Request) -> Response:
        # Not entered: the window of the application asks the login, the catalog is open after it.
        return HTMLResponse(CATALOG) if await self._entered(request) else RedirectResponse("/")

    async def find(self, request: Request) -> JSONResponse:
        if not await self._entered(request):
            return JSONResponse({"error": "login_required"}, status_code=401)
        total, found = self.snapshots.find(request.query_params.get("q", ""))
        return JSONResponse({"total": total, "shown": len(found), "rows": [
            {"n": each["n"], "torrent": each["torrent"], "files": each["files"], "size": each["size"],
             "series_id": (self.taken.get(str(each["n"])) or {}).get("series_id")} for each in found]})

    async def take(self, request: Request) -> JSONResponse:
        """Snapshot `n` as the source of a series of its own: the page, the series, the download (the user's path)."""
        n = int((await request.json())["n"])
        who = self._who(request)
        if not await self._entered(request):
            return JSONResponse({"error": "login_required"}, status_code=401)
        async with self._take:
            kept = self.taken.get(str(n))
            if kept and (await self.api.get(f"/api/series/{kept['series_id']}", headers=who)).status_code < 400:
                return JSONResponse({"series_id": kept["series_id"]})
            alias, h = self._list(n)
            url = PAGE + alias
            made = await self._call("POST", "/api/series", {
                "name": self.pages[alias]["name"]["main"], "name_en": alias, "site": "anilibria",
                "save_path": os.path.join(self.root, "library"), "auto_scan_enabled": False,
                "sources": [{"url": url, "source_type": "torrent"}]}, who)
            release = (await self._call("POST", "/api/torrent/page", {"url": url}, who))["releases"][0]["release"]
            await self._call("POST", "/api/torrent/download", {"url": url, "release": release}, who)
            self.taken[str(n)] = {"alias": alias, "infohash": h, "series_id": made["series_id"]}
            part = self.taken_path + ".part"
            with open(part, "w", encoding="utf-8") as fh:
                json.dump(self.taken, fh, ensure_ascii=False)
            os.replace(part, self.taken_path)
            return JSONResponse({"series_id": made["series_id"]})


CATALOG = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Симулятор торрентов</title><style>
:root { color-scheme: light; --line: #dee2e6; --muted: #6c757d; --blue: #0d6efd; }
body { margin: 0; padding: 16px; font: 14px/1.4 system-ui, sans-serif; background: #f8f9fa; color: #212529; }
header { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; margin-bottom: 12px; }
h1 { font-size: 18px; margin: 0; } a { color: var(--blue); }
input { flex: 1 1 320px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; font: inherit; }
.note { color: var(--muted); margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }
td, th { padding: 6px 10px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }
th { background: #f1f3f5; font-weight: 500; } td.num { color: var(--muted); white-space: nowrap; }
td.name { overflow-wrap: anywhere; user-select: text; }
button { padding: 4px 12px; border: 1px solid var(--blue); border-radius: 6px; background: #fff; color: var(--blue); cursor: pointer; font: inherit; }
button:disabled { opacity: .5; cursor: default; } .taken { color: #198754; white-space: nowrap; }
</style></head><body>
<header><h1>Симулятор вкладки «Торрент»</h1><input id="q" placeholder="Поиск по имени .torrent" autofocus>
<a href="/" target="_blank">Приложение симулятора</a></header>
<p class="note" id="count"></p>
<table><thead><tr><th>Раздача</th><th>Файлов</th><th>Размер</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<script>
const q = document.getElementById("q"), rows = document.getElementById("rows"), count = document.getElementById("count");
const size = (b) => b >= 2 ** 30 ? (b / 2 ** 30).toFixed(1) + " ГБ" : (b / 2 ** 20).toFixed(0) + " МБ";
let asked = 0;
async function find() {
  const mine = ++asked;
  const got = await (await fetch("/sim/api/find?q=" + encodeURIComponent(q.value))).json();
  if (mine !== asked) return;
  count.textContent = `Найдено ${got.total}` + (got.total > got.shown ? `, показаны первые ${got.shown}` : "");
  rows.replaceChildren(...got.rows.map((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="name"></td><td class="num">${r.files}</td><td class="num">${size(r.size)}</td><td></td>`;
    tr.children[0].textContent = r.torrent;
    tr.children[3].append(r.series_id ? taken() : button(r, tr.children[3]));
    return tr;
  }));
}
function taken() { const s = document.createElement("span"); s.className = "taken"; s.textContent = "в приложении"; return s; }
function button(r, cell) {
  const b = document.createElement("button"); b.textContent = "Взять";
  b.onclick = async () => {
    b.disabled = true; b.textContent = "…";
    const answer = await fetch("/sim/api/take", { method: "POST", headers: { "content-type": "application/json" },
                                                  body: JSON.stringify({ n: r.n }) });
    if (answer.ok) cell.replaceChildren(taken()); else { b.disabled = false; b.textContent = "Ошибка, ещё раз"; }
  };
  return b;
}
let pause; q.addEventListener("input", () => { clearTimeout(pause); pause = setTimeout(find, 250); });
find();
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--root", default=str(ROOT / "data" / "sim" / "torrent"))
    parser.add_argument("--snapshots", default=sorted(glob.glob(str(ROOT / "data" / "raw" / "torrent_files__*.jsonl")))[-1])
    args = parser.parse_args()
    os.chdir(ROOT)  # the gateway serves web/dist from the folder it runs in
    # The administrator of the service is the one of the simulator: the owner enters it with the login he knows.
    for line in open(ROOT / ".env", encoding="utf-8"):
        key, _, value = line.strip().partition("=")
        if key in ("ST_ADMIN_USER", "ST_ADMIN_PASSWORD"):
            os.environ[key] = value.strip("'\"")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request of the models would bury the log
    asyncio.run(Simulator(args.root, args.snapshots).start(args.port))


if __name__ == "__main__":
    main()
