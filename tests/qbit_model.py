"""The model of qBittorrent on its WebAPI v2, as the stand meets it over real HTTP, and the synthetic releases it is told of.

What the model does is what the real client was measured to do (tools/collectors/qbit_behaviour.py, qBittorrent 5.2.1;
the observations are kept in tests/samples/qbit/update_5_2_1.json and replayed by tests/test_qbit_model.py):

- a torrent added stopped does not look at the disk: its progress is 0 until a recheck;
- a recheck hashes the pieces from the files on disk, writes no data, leaves a stopped torrent stopped, and renames a file
  left incomplete to `<name>.!qB` (the client's `incomplete_files_ext`) and a complete one back;
- a file with priority 0 is never created; a start with nobody seeding writes nothing;
- a deletion with files removes the torrent's own files (`.!qB` ones too), never a file of the folder it does not own.

The swarm is the test's: `seed()` gives the data of a release; a running torrent then downloads every wanted piece at once,
or on `deliver()` when the release is seeded on hold — the moment a download ends is the test's word, not a pause.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import threading
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from modules.torrents.infohash import infohash_from_magnet

PIECE = 256 * 1024
INCOMPLETE = ".!qB"


# --- synthetic releases ------------------------------------------------------------------------------------------------

def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    keys = sorted(value, key=lambda k: k.encode() if isinstance(k, str) else k)
    return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in keys) + b"e"


def bdecode(data: bytes, pos: int = 0):
    """(value, end); dictionaries keep str keys, strings stay bytes."""
    ch = data[pos:pos + 1]
    if ch == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if ch in (b"l", b"d"):
        items, pos = [], pos + 1
        while data[pos:pos + 1] != b"e":
            item, pos = bdecode(data, pos)
            items.append(item)
        if ch == b"l":
            return items, pos + 1
        return {items[i].decode(): items[i + 1] for i in range(0, len(items), 2)}, pos + 1
    colon = data.index(b":", pos)
    start = colon + 1
    return data[start:start + int(data[pos:colon])], start + int(data[pos:colon])


def payload(seed: str, size: int) -> bytes:
    """Deterministic content: the same seed is the same file in every version and on every run."""
    out, block = bytearray(), hashlib.sha256(seed.encode()).digest()
    while len(out) < size:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:size])


def make_torrent(files: list[tuple[str, bytes]], *, piece: int = PIECE) -> tuple[bytes, str]:
    """A multi-file v1 torrent without padding files (the corpus norm: 10 of 12574 have them); paths start with the root
    folder, which is the torrent's name. Returns (.torrent, infohash)."""
    root = files[0][0].split("/")[0]
    data = b"".join(content for _, content in files)
    pieces = b"".join(hashlib.sha1(data[i:i + piece]).digest() for i in range(0, len(data), piece))
    info = {"name": root, "piece length": piece, "pieces": pieces,
            # Paths inside the info are relative to the root name.
            "files": [{"length": len(c), "path": p.split("/")[1:]} for p, c in files]}
    return bencode({"info": info, "created by": "st-model"}), hashlib.sha1(bencode(info)).hexdigest()


def magnet_of(infohash: str, name: str = "") -> str:
    return f"magnet:?xt=urn:btih:{infohash}" + (f"&dn={name}" if name else "")


# --- the model -----------------------------------------------------------------------------------------------------

@dataclass
class _File:
    path: str           # as the client names it: root/…
    size: int
    offset: int
    priority: int = 1


@dataclass
class _Torrent:
    hash: str
    save_path: str
    category: str
    stopped: bool
    name: str = ""
    files: list[_File] = field(default_factory=list)
    piece: int = PIECE
    hashes: list[bytes] = field(default_factory=list)
    have: set[int] = field(default_factory=set)
    added_on: int = 0

    @property
    def meta(self) -> bool:
        return bool(self.files)

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)

    def span(self, f: _File) -> tuple[int, int]:
        last = (f.offset + f.size - 1) // self.piece if f.size else f.offset // self.piece
        return f.offset // self.piece, last

    def done_bytes(self, f: _File) -> int:
        first, last = self.span(f)
        got = 0
        for i in range(first, last + 1):
            if i in self.have:
                lo, hi = max(f.offset, i * self.piece), min(f.offset + f.size, (i + 1) * self.piece)
                got += max(0, hi - lo)
        return got

    def wanted(self) -> list[_File]:
        return [f for f in self.files if f.priority > 0]

    def complete(self) -> bool:
        return all(self.done_bytes(f) == f.size for f in self.wanted())


@dataclass
class _Release:
    meta: bytes
    contents: dict[str, bytes]
    hold: bool


class QbitModel:
    """qBittorrent on its WebAPI: `app` is served by the test (tests/app_stand.Served); paths are the same for the client and
    the application, as `/nas` is on the server."""

    def __init__(self, *, username: str = "admin", password: str = "model", incomplete_ext: bool = True) -> None:
        self.username, self.password = username, password
        self.incomplete_ext = incomplete_ext
        self.torrents: dict[str, _Torrent] = {}
        self.categories: dict[str, str] = {}
        self.swarm: dict[str, _Release] = {}
        self.sessions: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.RLock()
        self._clock = 1_700_000_000
        self.app = Starlette(routes=[Route("/api/v2/{group}/{name}", self._dispatch, methods=["GET", "POST"])])

    # --- what the world outside does (the test) ---------------------------------------------------------------

    def seed(self, files: list[tuple[str, bytes]], *, hold: bool = False) -> tuple[bytes, str]:
        """Somebody seeds this release from now on; returns its (.torrent, infohash)."""
        meta, h = make_torrent(files)
        with self._lock:
            self.swarm[h] = _Release(meta, dict(files), hold)
            self._advance(h)
        return meta, h

    def deliver(self, h: str) -> None:
        """The held download of a seeded release ends now."""
        with self._lock:
            self.swarm[h].hold = False
            self._advance(h)

    def unseed(self, h: str) -> None:
        with self._lock:
            self.swarm.pop(h, None)

    # --- the client's own behaviour -----------------------------------------------------------------------------

    def _disk(self, t: _Torrent, f: _File) -> Path | None:
        base = Path(t.save_path) / f.path
        for candidate in (base, base.with_name(base.name + INCOMPLETE)):
            if candidate.exists():
                return candidate
        return None

    def _settle_names(self, t: _Torrent) -> None:
        for f in t.files:
            where = self._disk(t, f)
            if where is None:
                continue
            want = Path(t.save_path) / f.path
            if self.incomplete_ext and t.done_bytes(f) < f.size:
                want = want.with_name(want.name + INCOMPLETE)
            if where != want:
                where.rename(want)

    def _read_piece(self, t: _Torrent, i: int) -> bytes | None:
        lo, hi, out = i * t.piece, min((i + 1) * t.piece, t.total), bytearray()
        for f in t.files:
            a, b = max(lo, f.offset), min(hi, f.offset + f.size)
            if a >= b:
                continue
            where = self._disk(t, f)
            if where is None or where.stat().st_size < f.size:
                return None
            with where.open("rb") as fh:
                fh.seek(a - f.offset)
                out += fh.read(b - a)
        return bytes(out)

    def _recheck(self, t: _Torrent) -> None:
        t.have = {i for i in range(len(t.hashes))
                  if (got := self._read_piece(t, i)) is not None and hashlib.sha1(got).digest() == t.hashes[i]}
        self._settle_names(t)

    def _attach_meta(self, t: _Torrent, meta: bytes) -> None:
        info = bdecode(meta)[0]["info"]
        name = info["name"].decode()
        t.name, t.piece = name, info["piece length"]
        t.hashes = [info["pieces"][i:i + 20] for i in range(0, len(info["pieces"]), 20)]
        offset, t.files = 0, []
        for entry in info["files"]:
            path = "/".join([name] + [p.decode() for p in entry["path"]])
            t.files.append(_File(path, entry["length"], offset))
            offset += entry["length"]

    def _advance(self, h: str) -> None:
        """A running torrent of a seeded release takes what the swarm gives: metadata, then every wanted piece."""
        t, release = self.torrents.get(h), self.swarm.get(h)
        if t is None or release is None or t.stopped:
            return
        if not t.meta:
            self._attach_meta(t, release.meta)
        if release.hold:
            return
        data = b"".join(release.contents[f.path] for f in t.files)
        for i in range(len(t.hashes)):
            lo, hi = i * t.piece, min((i + 1) * t.piece, t.total)
            touched = [f for f in t.files if max(lo, f.offset) < min(hi, f.offset + f.size)]
            if i in t.have or not any(f.priority > 0 for f in touched):
                continue
            for f in touched:
                if f.priority == 0:
                    continue  # the client keeps such bytes in its part file, never in the file
                a, b = max(lo, f.offset), min(hi, f.offset + f.size)
                where = self._disk(t, f)
                if where is None:
                    where = Path(t.save_path) / f.path
                    where = where.with_name(where.name + INCOMPLETE) if self.incomplete_ext else where
                    where.parent.mkdir(parents=True, exist_ok=True)
                    where.write_bytes(b"\0" * f.size)
                with where.open("r+b") as fh:
                    fh.seek(a - f.offset)
                    fh.write(data[a:b])
            t.have.add(i)
        self._settle_names(t)

    def _state(self, t: _Torrent) -> str:
        if not t.meta:
            return "stoppedDL" if t.stopped else "metaDL"
        done = t.complete()
        if t.stopped:
            return "stoppedUP" if done else "stoppedDL"
        if done:
            return "stalledUP"
        release = self.swarm.get(t.hash)
        return "downloading" if release is not None else "stalledDL"

    def _info(self, t: _Torrent) -> dict:
        # The torrent's size and what is left are counted by pieces: a piece is wanted when it touches a wanted file
        # (measured: E03 off → amount_left 1058740 = 13 pieces + the last one − 9 had); a file's progress is by bytes.
        wanted = set()
        for f in t.wanted():
            first, last = t.span(f)
            wanted.update(range(first, last + 1))
        sizes = {i: min((i + 1) * t.piece, t.total) - i * t.piece for i in wanted}
        size = sum(sizes.values())
        left = sum(n for i, n in sizes.items() if i not in t.have)
        root = Path(t.save_path) / t.name if t.name else Path(t.save_path)
        return {"hash": t.hash, "name": t.name or t.hash, "state": self._state(t), "category": t.category,
                "save_path": t.save_path, "content_path": str(root), "size": size, "total_size": t.total,
                "progress": (size - left) / size if size else (1.0 if t.meta else 0.0), "amount_left": left,
                "dlspeed": 0, "upspeed": 0, "eta": 8640000, "added_on": t.added_on, "num_seeds": 0}

    def _files(self, t: _Torrent) -> list[dict]:
        return [{"index": i, "name": f.path, "size": f.size, "priority": f.priority, "piece_range": list(t.span(f)),
                 "progress": t.done_bytes(f) / f.size if f.size else 1.0, "is_seed": False, "availability": 0}
                for i, f in enumerate(t.files)]

    def _delete(self, t: _Torrent, with_files: bool) -> None:
        self.torrents.pop(t.hash, None)
        if not with_files:
            return
        dirs: set[Path] = set()
        for f in t.files:
            where = self._disk(t, f)
            if where is not None:
                where.unlink()
                dirs.update(p for p in where.parents if Path(t.save_path) in p.parents)
        for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()

    # --- the WebAPI ---------------------------------------------------------------------------------------------

    async def _form(self, request: Request) -> tuple[dict[str, str], list[bytes]]:
        body = await request.body()
        kind = request.headers.get("content-type", "")
        if kind.startswith("multipart/"):
            message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + kind.encode() + b"\r\n\r\n" + body)
            fields, blobs = {}, []
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    blobs.append(part.get_payload(decode=True))
                else:
                    fields[name] = part.get_payload(decode=True).decode()
            return fields, blobs
        query = dict(request.query_params)
        query.update({k: v[-1] for k, v in parse_qs(body.decode()).items()})
        return query, []

    async def _dispatch(self, request: Request) -> Response:
        group, name = request.path_params["group"], request.path_params["name"]
        form, blobs = await self._form(request)
        with self._lock:
            self.calls.append((f"{group}/{name}", form))
            if (group, name) == ("auth", "login"):
                if form.get("username") != self.username or form.get("password") != self.password:
                    return PlainTextResponse("Fails.")
                sid = secrets.token_hex(8)
                self.sessions.add(sid)
                response = Response(status_code=204)
                response.set_cookie("SID", sid)
                return response
            if request.cookies.get("SID") not in self.sessions:
                return PlainTextResponse("Forbidden", status_code=403)
            handler = getattr(self, f"_api_{group}_{name}", None)
            if handler is None:
                return PlainTextResponse("Not Found", status_code=404)
            return handler(form, blobs)

    def _api_app_version(self, form, blobs):
        return PlainTextResponse("v5.2.1")

    def _api_app_webapiVersion(self, form, blobs):  # noqa: N802 — the name of the protocol
        return PlainTextResponse("2.15.1")

    def _api_app_preferences(self, form, blobs):
        return JSONResponse({"save_path": "/downloads", "incomplete_files_ext": self.incomplete_ext,
                             "preallocate_all": False, "add_stopped_enabled": False, "auto_tmm_enabled": False,
                             "torrent_content_layout": "Original", "use_unwanted_folder": False,
                             "temp_path_enabled": False, "recheck_completed_torrents": False})

    def _api_torrents_add(self, form, blobs):
        stopped = (form.get("stopped") or form.get("paused") or "false") == "true"
        added = []
        for blob in blobs:
            info = bdecode(blob)[0]["info"]
            added.append((hashlib.sha1(bencode(info)).hexdigest(), blob))
        for url in filter(None, (form.get("urls") or "").splitlines()):
            added.append((infohash_from_magnet(url.strip()), None))
        if any(h in self.torrents for h, _ in added):
            return PlainTextResponse("Conflict", status_code=409)
        for h, blob in added:
            self._clock += 1
            t = _Torrent(h, form.get("savepath") or "/downloads", form.get("category") or "", stopped, added_on=self._clock)
            if blob is not None:
                self._attach_meta(t, blob)
            self.torrents[h] = t
            if t.category:
                self.categories.setdefault(t.category, "")
            self._advance(h)
        return PlainTextResponse("Ok.")

    def _pick(self, form) -> list[_Torrent]:
        wanted = form.get("hashes") or form.get("hash") or ""
        if wanted == "all":
            return list(self.torrents.values())
        return [self.torrents[h] for h in wanted.split("|") if h in self.torrents]

    def _api_torrents_info(self, form, blobs):
        chosen = self._pick(form) if form.get("hashes") else list(self.torrents.values())
        if "category" in form:
            chosen = [t for t in chosen if t.category == form["category"]]
        return JSONResponse([self._info(t) for t in chosen])

    def _api_torrents_files(self, form, blobs):
        t = self.torrents.get(form.get("hash", ""))
        return JSONResponse(self._files(t)) if t else PlainTextResponse("Not Found", status_code=404)

    def _api_torrents_pieceStates(self, form, blobs):  # noqa: N802
        t = self.torrents.get(form.get("hash", ""))
        if t is None:
            return PlainTextResponse("Not Found", status_code=404)
        return JSONResponse([2 if i in t.have else 0 for i in range(len(t.hashes))])

    def _api_torrents_filePrio(self, form, blobs):  # noqa: N802
        t = self.torrents.get(form.get("hash", ""))
        if t is None:
            return PlainTextResponse("Not Found", status_code=404)
        for i in (int(x) for x in form["id"].split("|")):
            t.files[i].priority = int(form["priority"])
        self._advance(t.hash)
        return Response(status_code=200)

    def _run(self, form, stopped: bool):
        for t in self._pick(form):
            t.stopped = stopped
            self._advance(t.hash)
        return Response(status_code=200)

    def _api_torrents_stop(self, form, blobs):
        return self._run(form, True)

    def _api_torrents_start(self, form, blobs):
        return self._run(form, False)

    def _api_torrents_recheck(self, form, blobs):
        for t in self._pick(form):
            self._recheck(t)
            self._advance(t.hash)
        return Response(status_code=200)

    def _api_torrents_delete(self, form, blobs):
        for t in self._pick(form):
            self._delete(t, form.get("deleteFiles") == "true")
        return Response(status_code=200)

    def _api_torrents_renameFile(self, form, blobs):  # noqa: N802
        t = self.torrents.get(form.get("hash", ""))
        f = next((f for f in (t.files if t else []) if f.path == form.get("oldPath")), None)
        if f is None:
            return PlainTextResponse("Conflict", status_code=409)
        where = self._disk(t, f)
        f.path = form["newPath"]
        if where is not None:
            target = Path(t.save_path) / f.path
            target = target.with_name(target.name + where.suffix) if where.name.endswith(INCOMPLETE) else target
            target.parent.mkdir(parents=True, exist_ok=True)
            where.rename(target)
        return Response(status_code=200)

    def _api_torrents_setLocation(self, form, blobs):  # noqa: N802
        for t in self._pick(form):
            for f in t.files:
                where = self._disk(t, f)
                if where is not None:
                    target = Path(form["location"]) / where.relative_to(t.save_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    where.rename(target)
            t.save_path = form["location"]
        return Response(status_code=200)

    def _api_torrents_categories(self, form, blobs):
        return JSONResponse({n: {"name": n, "savePath": p} for n, p in self.categories.items()})

    def _api_torrents_createCategory(self, form, blobs):  # noqa: N802
        if form["category"] in self.categories:
            return PlainTextResponse("Conflict", status_code=409)
        self.categories[form["category"]] = form.get("savePath", "")
        return Response(status_code=200)

    def _api_torrents_removeCategories(self, form, blobs):  # noqa: N802
        for n in (form.get("categories") or "").split("\n"):
            self.categories.pop(n, None)
        return Response(status_code=200)


# --- the update scenario: recorded on the real client, replayed on the model -----------------------------------------

CHECKING = {"checkingUP", "checkingDL", "checkingResumeData", "moving", "allocating"}
SIZES = {"E01": PIECE * 5 + 1000, "E02": PIECE * 4 + 7777, "E03": PIECE * 3 + 333, "old": PIECE * 2 + 55,
         "snd": PIECE + 999}
V1 = [("Show/E01.mkv", ("E01", SIZES["E01"])), ("Show/E02.mkv", ("E02", SIZES["E02"])),
      ("Show/old_name.mkv", ("E04", SIZES["old"])), ("Show/Sound/E01.mka", ("snd1", SIZES["snd"]))]
# v2: E01 and E02 unchanged, E03 inserted after E02, old_name renamed to E04, the external sound of E01 replaced in place.
V2 = [("Show/E01.mkv", ("E01", SIZES["E01"])), ("Show/E02.mkv", ("E02", SIZES["E02"])),
      ("Show/E03.mkv", ("E03", SIZES["E03"])), ("Show/E04.mkv", ("E04", SIZES["old"])),
      ("Show/Sound/E01.mka", ("snd1-new", SIZES["snd"]))]


def release(layout) -> list[tuple[str, bytes]]:
    return [(path, payload(seed, size)) for path, (seed, size) in layout]


def disk(folder: Path) -> list[dict]:
    if not folder.exists():
        return []
    return [{"path": str(p.relative_to(folder)), "dir": p.is_dir(), "size": 0 if p.is_dir() else p.stat().st_size,
             "mtime_ns": 0 if p.is_dir() else p.stat().st_mtime_ns} for p in sorted(folder.rglob("*"))]


class UpdateScenario:
    """v1 complete on disk → told to the client and rechecked; v1 leaves without files, v2 comes on the same files and is
    rechecked; priority 0 for the new file, a start with nobody seeding, a stop; v2 deleted with its files."""

    def __init__(self, client, folder: Path, save_path: str, record, *, category: str = "st-probe") -> None:
        self.client, self.folder, self.save_path, self.record = client, folder, save_path, record
        self.category = category
        self.hashes: list[str] = []

    async def observe(self, scenario: str, step: str, h: str | None) -> None:
        listed = await self.client.torrents_info([h]) if h else []
        info = listed[0] if listed else None
        files = await self.client.torrent_files(h) if info else None
        pieces = None
        if info:
            got = await self.client._request("GET", "/api/v2/torrents/pieceStates", params={"hash": h})
            pieces = got.json() if got else None
        keep = ("state", "progress", "amount_left", "size", "total_size")
        self.record(scenario, step, hash=h, info={k: info.get(k) for k in keep} if info else None,
                    files=[{k: f.get(k) for k in ("index", "name", "size", "progress", "priority", "piece_range")}
                           for f in files] if files else None,
                    pieces=pieces, disk=disk(self.folder))

    async def settle(self, h: str, until, *, budget_s: float = 60.0) -> dict:
        """The WebAPI pushes nothing: the state is read until the awaited one; the budget is a safeguard only."""
        deadline = time.monotonic() + budget_s
        while True:
            got = await self.client.torrents_info([h])
            if got and until(got[0]):
                return got[0]
            if time.monotonic() > deadline:
                raise TimeoutError(f"{h}: {got[0]['state'] if got else 'absent'}")
            await asyncio.sleep(0.05)

    async def add(self, meta: bytes, h: str) -> None:
        got = await self.client._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True,
            data={"savepath": self.save_path, "category": self.category, "paused": "true", "stopped": "true"},
            files={"torrents": ("probe.torrent", meta, "application/x-bittorrent")})
        if got.status_code == 409:
            raise RuntimeError(f"{h} already in the client")
        await self.settle(h, lambda t: t["state"] not in CHECKING)

    async def recheck(self, scenario: str, h: str) -> None:
        before = (await self.client.torrents_info([h]))[0]["progress"]
        await self.client.recheck([h])
        # The end of a check is the check seen running or the progress moved; a check too small to be seen between two
        # reads is recorded as such instead of guessed.
        try:
            await self.settle(h, lambda t: t["state"] in CHECKING or t["progress"] != before, budget_s=10)
            await self.settle(h, lambda t: t["state"] not in CHECKING)
        except TimeoutError as exc:
            self.record(scenario, "recheck_unobserved", hash=h, detail=str(exc), progress_before=before)

    async def gone(self, h: str) -> None:
        deadline = time.monotonic() + 30
        while await self.client.torrents_info([h]):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{h} still listed")
            await asyncio.sleep(0.05)

    async def run(self) -> list[str]:
        v1, v2 = release(V1), release(V2)
        meta1, h1 = make_torrent(v1)
        meta2, h2 = make_torrent(v2)
        self.hashes = [h1, h2]
        self.record("env", "layout", v1=[[p, len(c)] for p, c in v1], v2=[[p, len(c)] for p, c in v2],
                    piece=PIECE, h1=h1, h2=h2)
        self.folder.mkdir(mode=0o777)
        self.folder.chmod(0o777)
        for rel, content in v1:
            target = self.folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o666)
            for parent in target.relative_to(self.folder).parents:
                (self.folder / parent).chmod(0o777)
        await self.observe("A", "disk_before_add", None)
        await self.add(meta1, h1)
        await self.observe("A", "added_stopped", h1)
        await self.recheck("A", h1)
        await self.observe("A", "rechecked", h1)

        await self.client.delete([h1], delete_files=False)
        await self.gone(h1)
        await self.observe("B", "v1_deleted_keep_files", None)
        await self.add(meta2, h2)
        await self.observe("B", "v2_added_stopped", h2)
        await self.recheck("B", h2)
        await self.observe("B", "v2_rechecked", h2)

        files = await self.client.torrent_files(h2)
        await self.client.set_file_priority(h2, [f["index"] for f in files if f["name"].endswith("E03.mkv")], 0)
        await self.observe("C", "e03_priority_0", h2)
        await self.client.resume([h2])
        await self.settle(h2, lambda t: t["state"] not in CHECKING and not t["state"].startswith("stopped"))
        await self.observe("C", "started", h2)
        await self.client.pause([h2])
        await self.settle(h2, lambda t: t["state"].startswith("stopped"))
        await self.observe("C", "stopped_again", h2)

        await self.client.delete([h2], delete_files=True)
        await self.gone(h2)
        await self.observe("D", "v2_deleted_with_files", None)
        return [h1, h2]
