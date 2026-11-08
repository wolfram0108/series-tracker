"""The world of the planner on a disk (stage 12, the table of an episode): the same plan (core/plan) carried out with real
files and the service's own code where it has it.

- Every video and voice-over is a small real file made by ffmpeg; what it truly is (a season and an episode) is in its
  content: the colour of the picture and the tone of the sound (`Codec`). The checks read the files back.
- A site is an HTTP server with those files (`Host`); the service's own downloader (modules/downloads/ytdlp) fetches them.
- The alignment service is a model (`Daemon`) that speaks the protocol of the real one — the same routes, bodies,
  schema, jobs and results — and answers with files it makes: a voice-over is laid onto a video only when its sound is
  the episode of the picture; a cut takes the episode out of a compilation by its picture. The service's own client
  (modules/conform/client.DaemonClient) talks to it.
- A container is built by the service's own `core.container.build`, with the provenance of what it holds attached.
- A sync applies by a journal: the new containers are built aside, then the journal is written and carried out —
  hide, remove, rename, publish. A crash at any step (`crash_at`) leaves the journal; `recover()` (the restart) carries it
  out to the end, or, when the crash came before the journal, keeps the snapshot as it was.
- At the start of every sync the table of the asset is checked against the disk: a file that is gone is gone from the table.

`check()` adds to the checks of the table: the library holds exactly the files of the table; every file holds one episode
(its picture and every sound decoded agree); every file carries its provenance; the asset told again from the files alone
(adoption) is the table."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import threading
from dataclasses import asdict, replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from core import container as mkv
from core.plan.tables import Container, RowId, Snapshot, Track, Video
from modules.conform.client import DaemonClient, Unconfirmed
from modules.downloads import ytdlp
from plan_world import DOWNLOADS, World

RATE = 16000


def _run(*args: str) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=60).stdout


class Codec:
    """What a file truly is, in its content: the k-th truth is a colour of the picture and a tone of the sound."""

    def __init__(self) -> None:
        self._k: dict[int, int] = {}

    def k(self, truth: int) -> int:
        return self._k.setdefault(truth, len(self._k) + 1)

    @staticmethod
    def colour(k: int) -> tuple[int, int, int]:
        return (k * 67) % 180 + 40, (k * 113) % 180 + 40, (k * 29) % 180 + 40

    @staticmethod
    def tone(k: int) -> int:
        return 200 + 37 * k

    def make(self, path: str, truths: list[int]) -> None:
        """A file of one second per truth: its colour and its tone."""
        inputs, links = [], ""
        for number, truth in enumerate(truths):
            r, g, b = self.colour(self.k(truth))
            inputs += ["-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=32x18:d=1:r=4",
                       "-f", "lavfi", "-i", f"sine=frequency={self.tone(self.k(truth))}:sample_rate={RATE}:duration=1"]
            links += f"[{2 * number}:v][{2 * number + 1}:a]"
        _run(*inputs, "-filter_complex", f"{links}concat=n={len(truths)}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
             "-c:v", "mpeg4", "-q:v", "2", "-c:a", "aac", "-b:a", "96k", path)

    def _nearest(self, measure, value) -> int:
        truth, _ = min(((truth, measure(k, value)) for truth, k in self._k.items()), key=lambda each: each[1])
        return truth

    def picture(self, path: str, second: int = 0) -> int:
        raw = _run("-ss", f"{second + 0.5}", "-i", path, "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                   "-pix_fmt", "rgb24", "-")
        found = tuple(raw[:3])
        return self._nearest(lambda k, value: sum((a - b) ** 2 for a, b in zip(self.colour(k), value)), found)

    def sound(self, path: str, stream: int, second: int = 0) -> int:
        raw = _run("-ss", f"{second}", "-i", path, "-map", f"0:a:{stream}", "-t", "1", "-ac", "1", "-ar", str(RATE),
                   "-f", "s16le", "-")
        samples = np.frombuffer(raw, dtype=np.int16).astype(float)
        spectrum = np.abs(np.fft.rfft(samples))
        frequency = float(np.argmax(spectrum[1:]) + 1) * RATE / len(samples)
        return self._nearest(lambda k, value: abs(self.tone(k) - value), frequency)

    def seconds(self, path: str) -> int:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             check=True, capture_output=True, text=True, timeout=30).stdout
        return round(float(out.strip() or 0))


class Host:
    """A site: the files of its rows over HTTP. A row in `missing` answers 404 (gone for good), one in `flaky` 503."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.missing: set[str] = set()
        self.flaky: set[str] = set()
        host = self

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _refuse(self) -> bool:
                name = self.path.lstrip("/")
                if name in host.missing:
                    self.send_error(404)
                    return True
                if name in host.flaky:
                    self.send_error(503)
                    return True
                return False

            def do_GET(self) -> None:
                if not self._refuse():
                    super().do_GET()

            def do_HEAD(self) -> None:
                if not self._refuse():
                    super().do_HEAD()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=root))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/{name}"

    def close(self) -> None:
        self.server.shutdown()


class EnqueueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    dubs: list[str]
    out_dir: str | None = None
    keep_tmp: bool = False
    label: str | None = None
    audio_method: str | None = None
    drift_speed_pct: float | None = None
    fill_silence: bool = True
    autostart: bool = True
    ref_atrack: int = 0
    dub_atracks: list[int] | None = None


class ReverseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compilation: str
    episodes: list[str]
    out_dir: str | None = None
    extend_edges: bool = True
    keep_tmp: bool = False
    label: str | None = None
    audio_method: str | None = None
    drift_speed_pct: float | None = None
    autostart: bool = True
    ref_atrack: int = 0
    dub_atracks: list[int] | None = None


class Daemon:
    """A model of the alignment service on the protocol of the real one. `answers` by the label of a job: "mismatch",
    "critical", "suspect" or "lost" (the job is made, the answer never comes)."""

    def __init__(self, codec: Codec) -> None:
        self.codec = codec
        self.jobs: dict[str, dict] = {}
        self.answers: dict[str, str] = {}
        app = FastAPI()
        self.app = app

        @app.get("/health")
        def health() -> dict:
            return {"version": "model"}

        @app.get("/conform/device")
        def device() -> dict:
            return {"device": "cpu", "gpu": False, "name": "model"}

        @app.post("/conform/enqueue")
        def enqueue(body: EnqueueBody) -> dict:
            return self._answer(self._align(body), body.label)

        @app.post("/conform/reverse")
        def reverse(body: ReverseBody) -> list[dict]:
            jobs = [self._cut(body, episode) for episode in body.episodes]
            if self.answers.get(body.label or "") == "lost":
                raise HTTPException(status_code=502, detail="no answer")
            return jobs

        @app.get("/conform/jobs")
        def jobs() -> list[dict]:
            return list(self.jobs.values())

        @app.get("/conform/jobs/{job_id}")
        def job(job_id: str) -> dict:
            if job_id not in self.jobs:
                raise HTTPException(status_code=404)
            return self.jobs[job_id]

        @app.delete("/conform/jobs/{job_id}")
        def cancel(job_id: str) -> dict:
            if self.jobs.pop(job_id, None) is None:
                raise HTTPException(status_code=404)
            return {"cancelled": job_id}

    def client(self) -> DaemonClient:
        return DaemonClient("http://daemon.model", httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)))

    def _job(self, kind: str, label: str | None, dubs: list[str], results: list[dict]) -> dict:
        job_id = f"m{len(self.jobs) + 1:05d}"
        job = {"id": job_id, "kind": kind, "label": label, "status": "done", "progress": 1.0, "dubs": dubs,
               "results": results, "error": None}
        self.jobs[job_id] = job
        return job

    def _answer(self, job: dict, label: str | None) -> dict:
        if self.answers.get(label or "") == "lost":
            raise HTTPException(status_code=502, detail="no answer")
        return job

    def _align(self, body: EnqueueBody) -> dict:
        answer = self.answers.get(body.label or "")
        picture = self.codec.picture(body.ref)
        tracks = body.dub_atracks or [0] * len(body.dubs)
        results = []
        for dub, stream in zip(body.dubs, tracks):
            ok = self.codec.sound(dub, stream) == picture and answer != "mismatch"
            out = None
            if ok:
                out = os.path.join(body.out_dir, f"{os.path.basename(dub)}.a{stream}.flac")
                _run("-i", dub, "-map", f"0:a:{stream}", "-c:a", "flac", out)
            results.append({"dub": dub, "ok": ok, "out_path": out, "suspect": ["подозрительно"] if answer == "suspect" else [],
                            "critical": ["критично"] if answer == "critical" else [],
                            "error": None if ok else "звук озвучки не совпал с картинкой"})
        return self._job("episode", body.label, body.dubs, results)

    def _cut(self, body: ReverseBody, episode: str) -> dict:
        answer = self.answers.get(body.label or "")
        wanted = self.codec.picture(episode)
        inside = [self.codec.picture(body.compilation, second) for second in range(self.codec.seconds(body.compilation))]
        result = {"dub": episode, "ok": False, "out_path": None, "suspect": [], "critical": [], "error": "кадры не сопоставлены"}
        if wanted in inside:
            out = os.path.join(body.out_dir, f"{os.path.basename(episode)}.cut.mkv")
            second = inside.index(wanted)
            _run("-ss", str(second), "-i", body.compilation, "-t", "1", "-c:v", "mpeg4", "-q:v", "2", "-c:a", "aac", out)
            result = {"dub": episode, "ok": True, "out_path": out, "suspect": [],
                      "critical": ["критично"] if answer == "critical" else [], "error": None,
                      "from_ref_s": 1.0, "from_dub_s": 0.0}
        return self._job("reverse", body.label, [episode], [result])


# --- the table of the asset as JSON: in the database of the service, and attached to every container -----------------

def row_json(row: RowId | None):
    return None if row is None else asdict(row)


def container_json(container: Container) -> dict:
    return {"name": container.name, "off": container.off, "video": {
        "id": asdict(container.video.id), "snapshot": asdict(container.video.snapshot),
        "episode": list(container.video.episode), "timeline": asdict(container.video.timeline),
        "cut_of": row_json(container.video.cut_of)},
        "tracks": [{"dub": asdict(track.dub), "snapshot": asdict(track.snapshot), "episode": list(track.episode),
                    "aligned_to": asdict(track.aligned_to)} for track in container.tracks]}


def container_of(data: dict) -> Container:
    video = data["video"]
    return Container(data["name"], Video(RowId(**video["id"]), Snapshot(**video["snapshot"]), tuple(video["episode"]),
                                         RowId(**video["timeline"]), RowId(**video["cut_of"]) if video["cut_of"] else None),
                     tuple(Track(RowId(**track["dub"]), Snapshot(**track["snapshot"]), tuple(track["episode"]),
                                 RowId(**track["aligned_to"])) for track in data["tracks"]), data["off"])


def dub_key(dub: RowId) -> str:
    return f"{dub.source_id}:{dub.row_key}:{dub.video}"


class Crash(Exception):
    """The process stops here (power, a kill): what is on disk stays as it is."""


class DiskWorld(World):
    """The world of the plan with real files (see the module)."""

    def __init__(self, root: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.root = root
        for name in ("site", "library", "work", "staging"):
            os.makedirs(os.path.join(root, name), exist_ok=True)
        self.codec = Codec()
        self.host = Host(os.path.join(root, "site"))
        self.daemon = Daemon(self.codec)
        self.paths: dict[str, str] = {}  # step of the graph -> the file it made
        self.pending: list[tuple] = []  # the steps of the journal of this sync
        self.crash_at: int | None = None  # the step of the journal the process stops before (-1: before the journal)
        self.journal = os.path.join(root, "library", ".journal.json")
        self.table = os.path.join(root, "assets.json")
        self._write_table()

    def library(self, name: str) -> str:
        return os.path.join(self.root, "library", name)

    # --- the site ------------------------------------------------------------------------------------------------

    def put(self, source_id, key, episode, **kwargs):
        made = super().put(source_id, key, episode, **kwargs)
        name = f"{source_id}_{hashlib.md5(f'{key}|{made.id.video}'.encode()).hexdigest()[:10]}.mp4"
        entry = next(each for each in self.site[source_id] if each.row.id.row_key == key)
        if not os.path.exists(os.path.join(self.root, "site", name)):
            self.codec.make(os.path.join(self.root, "site", name), list(entry.content))
        entry.row = replace(entry.row, link=self.host.url(name))
        return entry.row

    def lose_file(self, name: str) -> None:
        if os.path.exists(self.library(name)):
            os.remove(self.library(name))

    # --- a sync ----------------------------------------------------------------------------------------------------

    def sync(self, title: str = ""):
        # The snapshot is the disk: a file the table names and the disk has not is gone from the table.
        gone = [name for name in self.containers if not os.path.exists(self.library(name))]
        for name in gone:
            del self.containers[name]
        if gone:
            self._write_table()  # the table of the asset says so at once, as the database of the service would
        self.host.missing = {row.link.rsplit("/", 1)[1] for source in self.site.values() for each in source
                             for row in [each.row] if row.id in self.download_fails}
        self.host.flaky = {row.link.rsplit("/", 1)[1] for source in self.site.values() for each in source
                           for row in [each.row] if row.id in self.transient}
        self.daemon.answers = {}
        for (slot, voice), answer in self.answers.items():
            self.daemon.answers[f"s{slot[0]:02d}e{slot[1]:02d} {voice}"] = answer
        for slot, voice in self.align_fails:
            self.daemon.answers[f"s{slot[0]:02d}e{slot[1]:02d} {voice}"] = "mismatch"
        for slot, answer in self.cut_answers.items():
            self.daemon.answers[f"cut s{slot[0]:02d}e{slot[1]:02d}"] = answer
        return super().sync(title)

    def _work(self, op_id: str, extension: str = ".mp4") -> str:
        return os.path.join(self.root, "work", hashlib.md5(op_id.encode()).hexdigest()[:12] + extension)

    def _do(self, op, results):
        if op.kind in DOWNLOADS:
            row = op.subject
            path = self._work(op.id)
            done, _ = asyncio.run(ytdlp.download(row.link, path, _quiet))
            if not done:
                if row.id in self.transient:
                    self._passing.add(op.id)
                return None
            self.paths[op.id] = path
            if op.kind == "download_video":
                video = Video(row.id, row.snapshot(), row.slot, row.id)
                self.content[video.key] = (self.codec.picture(path),)
                return video
            if op.kind == "download_dub":
                self.content[("dub", row.id)] = (self.codec.sound(path, 0),)
                return row
            return path
        if op.kind == "cut":
            way = op.subject
            ref2 = self.library(way.ref2.container.name) if way.ref2.kind == "asset" else self.paths[f"ref2:{op.slot}"]
            label = f"cut s{op.slot[0]:02d}e{op.slot[1]:02d}"
            result = self._daemon("reverse", {"compilation": results[f"compilation:{way.row.id}"], "episodes": [ref2],
                                              "out_dir": os.path.join(self.root, "work"), "extend_edges": True,
                                              "label": label}, op)
            if result is None:
                return None
            self.paths[op.id] = result
            video = Video(way.row.id, way.snapshot, op.slot, way.timeline, cut_of=way.row.id)
            self.content[video.key] = (self.codec.picture(result),)
            return video
        if op.kind == "align":
            goal, way = op.subject
            video_path = self._video_path(goal)
            if way.kind == "download":
                dub, stream = self.paths[f"dub:{goal.slot}:{way.dub}"], 0
            else:
                dub, stream = self.library(way.container.name), self._stream(way.container.name, way.dub, way.own)
            label = f"s{goal.slot[0]:02d}e{goal.slot[1]:02d} {way.voice}"
            result = self._daemon("enqueue", {"ref": video_path, "dubs": [dub], "dub_atracks": [stream],
                                              "out_dir": os.path.join(self.root, "work"), "label": label}, op)
            if result is None:
                return None
            self.paths[op.id] = result
            return Track(way.dub, way.snapshot, goal.slot, self._video_of(goal, results).timeline), (self.codec.sound(result, 0),)
        if op.kind == "build":
            return op.subject
        raise AssertionError(op.kind)

    def _daemon(self, route: str, body: dict, op) -> str | None:
        """The service's own client to the model of the service; the path of the result, or None when it failed."""
        async def ask():
            client = self.daemon.client()
            try:
                answer = await (client.enqueue(body) if route == "enqueue" else client.reverse(body))
                job = answer if isinstance(answer, dict) else answer[0]
                return await client.job(job["id"])
            finally:
                await client.close()
        try:
            job = asyncio.run(ask())
        except Unconfirmed:
            self._passing.add(op.id)  # the answer never came: the job is looked at again at the next sync
            return None
        result = job["results"][0]
        # The contract of the service: a result is taken by `ok` and an empty `critical`.
        return result["out_path"] if result["ok"] and not result["critical"] else None

    def _video_path(self, goal) -> str:
        if goal.video.kind == "asset":
            return self.library(goal.video.container.name)
        return self.paths[f"video:{goal.slot}" if goal.video.kind == "download" else f"cut:{goal.slot}"]

    def _stream(self, name: str, dub: RowId, own: bool) -> int:
        """The index of the audio stream of a track in a container, told by the container's own tags."""
        held = asyncio.run(mkv.contents(self.library(name)))
        for track in held.audio:
            if (own and track.own) or (not own and track.key == dub_key(dub)):
                return track.index
        raise AssertionError(f"{name} holds no track {dub} (own={own})")

    def _apply(self, made, group, results) -> None:
        for slot in group.built:
            goal = made.goal(slot)
            video = self._video_of(goal, results)
            video_path = self._video_path(goal)
            identity = mkv.identity(video.timeline.source_id, video.timeline.row_key)
            own_stream = self._stream(goal.video.container.name, video.id, True) if goal.video.kind == "asset" else 0
            tracks = [mkv.AudioIn(video.snapshot.group or "—", video_path, own_stream, identity, own=True)]
            made_tracks = []
            for way in goal.tracks:
                if way.kind == "reuse":
                    path = self.library(way.container.name)
                    stream = self._stream(way.container.name, way.dub, way.own)
                else:
                    path, stream = self.paths[f"align:{slot}:{way.voice}"], 0
                tracks.append(mkv.AudioIn(way.snapshot.group or way.voice, path, stream, identity, key=dub_key(way.dub)))
                made_tracks.append(Track(way.dub, way.snapshot, slot, video.timeline))
            built = Container(goal.name, replace(video, episode=slot), tuple(made_tracks), goal.off)
            staging = os.path.join(self.root, "staging", hashlib.md5(goal.name.encode()).hexdigest()[:12] + ".mkv")
            problem = asyncio.run(mkv.build(staging, video_path, identity, tracks, provenance=container_json(built)))
            assert problem is None, f"{goal.name}: {problem}"
            self.pending.append(("publish", staging, goal.name))
        for slot, name in group.kept:
            goal = made.goal(slot)
            if goal.name != name:
                self.pending.append(("move", name, goal.name))
        for name in group.remove:
            self.pending.append(("remove", name))
        for name, hidden in group.hide:
            self.pending.append(("move", name, hidden))
        super()._apply(made, group, results)
        # A kept file whose name changed carries the provenance of its new name: it is built anew under it.
        for slot, name in group.kept:
            goal = made.goal(slot)
            if goal.name != name:
                self.pending.append(("retag", goal.name))
        for _, hidden in group.hide:
            self.pending.append(("retag", hidden))

    def _commit(self) -> None:
        if self.crash_at == -1:
            raise Crash("before the journal")
        if not self.pending:
            self._clean()
            return
        moves = [step for step in self.pending if step[0] == "move"]
        # Out of the way first, then into place: two moves may swap names.
        steps = [("move", old, old + ".moving") for _, old, _ in moves]
        steps += [step for step in self.pending if step[0] == "remove"]
        steps += [("move", old + ".moving", new) for _, old, new in moves]
        steps += [step for step in self.pending if step[0] == "publish"]
        steps += [step for step in self.pending if step[0] == "retag"]
        table = [container_json(each) for each in sorted(self.containers.values(), key=lambda each: each.name)]
        with open(self.journal, "w") as out:
            json.dump({"steps": steps, "table": table}, out)
        self.pending = []
        self._carry_out(steps, table)

    def _carry_out(self, steps: list, table: list) -> None:
        for number, step in enumerate(steps):
            if self.crash_at == number:
                raise Crash(f"at step {number}: {step}")
            self._step(step, {each["name"]: each for each in table})
        with open(self.table, "w") as out:
            json.dump(table, out)
        os.remove(self.journal)
        self._clean()

    def _step(self, step, table: dict) -> None:
        """One step of the journal; carried out again after a crash, it does what is left of it."""
        if step[0] == "move":
            source, target = self.library(step[1]), self.library(step[2])
            if os.path.exists(source):
                os.replace(source, target)
        elif step[0] == "remove":
            if os.path.exists(self.library(step[1])):
                os.remove(self.library(step[1]))
        elif step[0] == "publish":
            if os.path.exists(step[1]):
                os.replace(step[1], self.library(step[2]))
        elif step[0] == "retag":
            # The file keeps its streams; its provenance follows its new name and state.
            path = self.library(step[1])
            if asyncio.run(mkv.provenance_of(path)) != table[step[1]]:
                again = path + ".retag"
                held = asyncio.run(mkv.contents(path))
                tracks = [mkv.AudioIn(each.label, path, each.index, each.aligned_to, own=each.own, key=each.key)
                          for each in held.audio]
                problem = asyncio.run(mkv.build(again, path, held.video, tracks, provenance=table[step[1]]))
                assert problem is None, problem
                os.replace(again, path)

    def recover(self) -> None:
        """The restart after a crash: a journal is carried out to the end; with no journal the snapshot stands."""
        self.crash_at = None
        self.pending = []
        if os.path.exists(self.journal):
            with open(self.journal) as found:
                journal = json.load(found)
            self._carry_out([tuple(step) for step in journal["steps"]], journal["table"])
        else:
            self._clean()
        with open(self.table) as found:
            self.containers = {each["name"]: container_of(each) for each in json.load(found)}

    def _clean(self) -> None:
        for name in ("work", "staging"):
            shutil.rmtree(os.path.join(self.root, name), ignore_errors=True)
            os.makedirs(os.path.join(self.root, name), exist_ok=True)
        self.paths = {}

    def _write_table(self) -> None:
        with open(self.table, "w") as out:
            json.dump([container_json(each) for each in self.containers.values()], out)

    # --- the checks of the disk --------------------------------------------------------------------------------

    def check(self, step=None) -> None:
        super().check(step)
        self.check_disk()

    def check_disk(self) -> None:
        """The disk alone: the files are the table, each holds one episode and carries its provenance; adoption."""
        files = sorted(name for name in os.listdir(os.path.join(self.root, "library")) if not name.startswith("."))
        assert files == sorted(self.containers), f"the library is not the table: {files} != {sorted(self.containers)}"
        with open(self.table) as found:
            stored = sorted(json.load(found), key=lambda each: each["name"])
        assert stored == [container_json(self.containers[name]) for name in sorted(self.containers)], "the table on disk"
        adopted = []
        for name in files:
            path = self.library(name)
            held = asyncio.run(mkv.contents(path))
            picture = self.codec.picture(path)
            for track in held.audio:
                assert self.codec.sound(path, track.index) == picture, f"{name}: a sound of another episode"
            provenance = asyncio.run(mkv.provenance_of(path))
            assert provenance == container_json(self.containers[name]), f"{name}: its provenance is not the table"
            adopted.append(container_of(provenance))
        # Adoption: the asset told again from the files alone is the table.
        assert {each.name: each for each in adopted} == self.containers, "the files do not tell the table"

    def close(self) -> None:
        self.host.close()


async def _quiet(_data) -> None:
    return None
