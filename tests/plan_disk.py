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
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import zlib
import threading
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from core import container as mkv
from core.plan.executor import DOWNLOADS, Made, Passing, judged, video_of
from core.plan.files import Library, container_json, container_of, dub_key, label_of  # noqa: F401
from core.plan.tables import Track, Video
from modules.conform.client import DaemonClient, Unconfirmed
from modules.conform.module import ABORT, finished_view
from modules.downloads import ytdlp
from plan_world import World

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

    @staticmethod
    def mark(path: str) -> int:
        """A quiet tone of the file itself, under the tone of its truth: two files are two recordings, as two voice-overs
        of one episode are in life, and the loud tone still tells the truth."""
        return 3000 + zlib.crc32(os.path.basename(path).encode()) % 3000

    def make(self, path: str, truths: list[int]) -> None:
        """A file of one second per truth: its colour and its tone, and the quiet tone of the file."""
        inputs, links = [], ""
        mark = self.mark(path)
        for number, truth in enumerate(truths):
            r, g, b = self.colour(self.k(truth))
            sound = f"0.125*sin(2*PI*{self.tone(self.k(truth))}*t)+0.01*sin(2*PI*{mark}*t)"
            inputs += ["-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=32x18:d=1:r=4",
                       "-f", "lavfi", "-i", f"aevalsrc={sound}:s={RATE}:d=1"]
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


# How long a file held in the middle waits for its release at most (a safeguard, not a synchronization).
HOLD_SAFEGUARD_S = 60.0


class Host:
    """A site: the files of its rows over HTTP. A row in `missing` answers 404 (gone for good), one in `flaky` 503."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.missing: set[str] = set()
        self.flaky: set[str] = set()
        self.served: list[str] = []  # the names of the files given out, in order: what was downloaded and how often
        # A file in `cut` is given out half and the connection closed, for as many asks as its number says.
        self.cut: dict[str, int] = {}
        # A file in `hold` is given out half and held there until `released` (a download caught in its middle, its
        # program alive); `holding` is set when such a file is being held.
        self.hold: set[str] = set()
        self.holding = threading.Event()
        self.released = threading.Event()
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
                if self._refuse():
                    return
                name = self.path.lstrip("/")
                host.served.append(name)
                if name not in host.hold and not host.cut.get(name):
                    super().do_GET()
                    return
                with open(os.path.join(root, name), "rb") as file:
                    body = file.read()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if host.cut.get(name):
                    host.cut[name] -= 1
                    self.wfile.write(body[:len(body) // 2])
                    self.close_connection = True
                    return
                try:
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    host.holding.set()
                    host.released.wait(HOLD_SAFEGUARD_S)  # a safeguard: a test that forgets to release does not hang
                    self.wfile.write(body[len(body) // 2:])
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the downloader went away: what a stopped download does

            def do_HEAD(self) -> None:
                if not self._refuse():
                    super().do_HEAD()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=root))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/{name}"

    def close(self) -> None:
        self.released.set()
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


# The trace of a pair the service turned down: a decision that went the way of `abort` (modules/conform/module.py).
REFUSED = [{"kind": "decision", "verdict": ABORT}]
TMP_DIR = "_tmp"  # the folder of the service's temporary files beside the sources of a job (conform/tmpfiles.py)


def _rmdir_if_empty(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass  # absent, or holding a run of another job


class Daemon:
    """A model of the alignment service on the protocol of the real one. `answers` by the label of a job: "mismatch",
    "critical", "suspect", "crash" (the service fell on the pair and judged nothing), "empty" (the job ends with no result
    for the voice-over: it did not come to the pair) or "lost" (the job is made, the answer never comes). Said for one
    voice-over of an episode, an answer may begin with "once ": it is given to the first pair only."""

    def __init__(self, codec: Codec) -> None:
        self.codec = codec
        self.jobs: dict[str, dict] = {}
        self.answers: dict[str, str] = {}
        # `hold`: a job stays running until `release` (a stop of the process can come in between); `made` is set when
        # a job is made.
        self.hold = False
        self.made = threading.Event()
        self.bodies: list[dict] = []  # every job body the daemon was sent, in order
        self._seq = 0  # the number of the last job: a removed job's number is not given again
        self.sources: dict[str, list[str]] = {}  # the files each job was given
        self.roots: dict[str, list[str]] = {}  # the run folders of each job still running
        app = FastAPI()
        self.app = app

        @app.get("/health")
        def health() -> dict:
            return {"version": "model"}

        @app.get("/conform/device")
        def device() -> dict:
            return {"device": "cpu", "gpu": False, "name": "model"}

        @app.get("/conform/settings")
        def settings() -> dict:
            # What the daemon accepts for the fields of its jobs, in the form of its GET /conform/settings.
            return {"limit": 4, "active": 0, "params": {"drift_speed_pct": {"min": 0.25, "max": 10.0, "default": 1.25},
                                                         "audio_method": {"values": ["band", "muq"], "default": "band"}}}

        @app.post("/conform/enqueue")
        def enqueue(body: EnqueueBody) -> dict:
            self.bodies.append(body.model_dump())
            job = self._align(body)
            if any(self._answer_for(body.label, dub) == "lost" for dub in body.dubs):
                raise HTTPException(status_code=502, detail="no answer")
            return self._answer(job, body.label)

        @app.post("/conform/reverse")
        def reverse(body: ReverseBody) -> list[dict]:
            self.bodies.append(body.model_dump())
            jobs = [self._cut(body, episode) for episode in body.episodes]
            if self._answer_of(body.label) == "lost":
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
            # As the real service removes a record (conform/queue.py `_drop_files`): every run folder under `_tmp`
            # beside the sources of the job goes, whichever job it is of.
            for folder in {os.path.dirname(source) for source in self.sources.pop(job_id, ())}:
                base = os.path.join(folder, TMP_DIR)
                for root in (glob.glob(os.path.join(glob.escape(base), "pair_*")) if os.path.isdir(base) else ()):
                    shutil.rmtree(root, ignore_errors=True)
                _rmdir_if_empty(base)
            return {"cancelled": job_id}

    def _answer_of(self, label: str | None) -> str | None:
        """The answer told for a job by its label; the caller's key at its end (` #key`) is not part of the name."""
        label = label or ""
        return self.answers.get(label, self.answers.get(re.sub(r"\s+#\S+$", "", label)))

    def _answer_for(self, label: str | None, dub: str) -> str | None:
        """The answer told for one voice-over of a job of an episode: by the label of the job and the voice-over its file
        is named by (`Сериал s01e01 AnimeVost`), else the answer of the whole job."""
        name, episode = os.path.basename(dub), re.sub(r"\s+#\S+$", "", label or "")
        for told, answer in list(self.answers.items()):
            if told.startswith(episode + " ") and told[len(episode) + 1:] in name:
                if answer.startswith("once "):  # told for the first pair of the voice-over only: the next one is asked afresh
                    del self.answers[told]
                    return answer[len("once "):]
                return answer
        return self._answer_of(label)

    def client(self) -> DaemonClient:
        return DaemonClient("http://daemon.model", httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)))

    def _job(self, kind: str, label: str | None, dubs: list[str], results: list[dict], sources: list[str] = ()) -> dict:
        """A job of these results. While it runs, its run folder lies under `_tmp` beside each of its `sources`, as the
        real service keeps its temporary files; a job whose run folder is gone by its end fails on it."""
        self._seq += 1
        job_id = f"m{self._seq:05d}"
        job = {"id": job_id, "kind": kind, "label": label, "status": "done", "progress": 1.0, "dubs": dubs,
               "results": results, "error": None}
        self.sources[job_id] = list(sources)
        self.roots[job_id] = sorted({os.path.join(os.path.dirname(source), TMP_DIR, f"pair_{job_id}")
                                     for source in sources})
        for root in self.roots[job_id]:
            os.makedirs(root, exist_ok=True)
        if self.hold:
            # As the real service tells a running job: the voice-over it is on, by its file name, and its place among them.
            job.update(status="running", progress=0.5, results=[], held=results, cur_dub=os.path.basename(dubs[0]) if dubs else "",
                       dub_index=1, dub_total=len(dubs))
        else:
            job["results"] = self._end(job_id, results)
        self.jobs[job_id] = job
        self.made.set()
        return job

    def _end(self, job_id: str, results: list[dict]) -> list[dict]:
        """What a job made at its end: nothing, when a run folder of it was removed under it. Its run folders go."""
        gone = [root for root in self.roots.pop(job_id, []) if not os.path.isdir(root)]
        if gone:
            results = [{"dub": each["dub"], "ok": False, "out_path": None, "suspect": [], "critical": [], "trace": [],
                        "error": f"[Errno 2] No such file or directory: '{gone[0]}/dub.f16'"} for each in results]
        for source in self.sources.get(job_id, ()):
            base = os.path.join(os.path.dirname(source), TMP_DIR)
            shutil.rmtree(os.path.join(base, f"pair_{job_id}"), ignore_errors=True)
            _rmdir_if_empty(base)
        return results

    def release(self) -> list[dict]:
        """The jobs held end now, with what they made."""
        ended = []
        for job in self.jobs.values():
            if "held" in job:
                job.update(status="done", progress=1.0, results=self._end(job["id"], job.pop("held")))
                ended.append(job)
        return ended

    def _answer(self, job: dict, label: str | None) -> dict:
        if self._answer_of(label) == "lost":
            raise HTTPException(status_code=502, detail="no answer")
        return job

    def _align(self, body: EnqueueBody) -> dict:
        os.makedirs(body.out_dir, exist_ok=True)  # the service makes the folder it writes in
        picture = self.codec.picture(body.ref)
        tracks = body.dub_atracks or [0] * len(body.dubs)
        results = []
        for dub, stream in zip(body.dubs, tracks):
            answer = self._answer_for(body.label, dub)
            if answer == "empty":
                continue  # the job ended without coming to this pair
            # The voice-over is laid onto the video where its sound is the episode of the picture: the whole of an
            # episode, or its stretch of a compilation (Д-19).
            sounds = [self.codec.sound(dub, stream, second) for second in range(max(1, self.codec.seconds(dub)))]
            ok = picture in sounds and answer not in ("mismatch", "crash")
            out = None
            if ok:
                out = os.path.join(body.out_dir, f"{os.path.basename(dub)}.a{stream}.flac")
                _run("-ss", str(sounds.index(picture)), "-i", dub, "-t", "1", "-map", f"0:a:{stream}", "-c:a", "flac", out)
            # The service names the voice-over of a result by its file name, though it is given the whole path.
            results.append({"dub": os.path.basename(dub), "ok": ok, "out_path": out, "suspect": ["подозрительно"] if answer == "suspect" else [],
                            "critical": ["критично"] if answer == "critical" else [],
                            # A crash in the service judged nothing; a pair it looked at and did not bring together is
                            # refused: its trace holds the decision `abort`, as the real service writes it.
                            "trace": REFUSED if not ok and answer != "crash" else [],
                            "error": None if ok else "сбой сервиса" if answer == "crash" else "звук озвучки не совпал с картинкой"})
        return self._job("episode", body.label, body.dubs, results, [body.ref, *body.dubs])

    def _cut(self, body: ReverseBody, episode: str) -> dict:
        os.makedirs(body.out_dir, exist_ok=True)  # the service makes the folder it writes in
        answer = self._answer_of(body.label)
        wanted = self.codec.picture(episode)
        inside = [self.codec.picture(body.compilation, second) for second in range(self.codec.seconds(body.compilation))]
        result = {"dub": episode, "ok": False, "out_path": None, "suspect": [], "critical": [], "trace": REFUSED,
                  "error": "кадры не сопоставлены"}
        if wanted in inside:
            out = os.path.join(body.out_dir, f"{os.path.basename(episode)}.cut.mkv")
            second = inside.index(wanted)
            # The picture and its own sound, the track the caller named (a file of the library holds several).
            _run("-ss", str(second), "-i", body.compilation, "-t", "1", "-map", "0:v:0", "-map", f"0:a:{body.ref_atrack}",
                 "-c:v", "mpeg4", "-q:v", "2", "-c:a", "aac", out)
            result = {"dub": episode, "ok": True, "out_path": out, "suspect": [],
                      "critical": ["критично"] if answer == "critical" else [], "error": None,
                      "from_ref_s": 1.0, "from_dub_s": 0.0}
        # The service labels the job of each episode by the folder of the episode: the key the caller finds it by.
        return self._job("reverse", os.path.basename(os.path.dirname(episode)), [episode], [result],
                         [body.compilation, episode])


class Crash(Exception):
    """The process stops here (power, a kill): what is on disk stays as it is."""


class DiskWorld(World):
    """The world of the plan with real files (see the module): the service's own sync, files and journal. What the world
    reads of a file to know its truth runs in a thread, as every blocking call of the service does: the loop is free."""

    def __init__(self, root: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.root = root
        for name in ("site", "library", "work"):
            os.makedirs(os.path.join(root, name), exist_ok=True)
        self.codec = Codec()
        self.host = Host(os.path.join(root, "site"))
        self.daemon = Daemon(self.codec)
        self.files = Library(os.path.join(root, "library"))
        self.crash_at: int | None = None  # the step of the journal the process stops before (-1: before the journal)
        self.table = os.path.join(root, "assets.json")
        self._write_table()

    def library(self, name: str) -> str:
        return self.files.path(name)

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
            self.daemon.answers[f"{label_of(slot)} {voice}"] = answer
        for slot, voice in self.align_fails:
            self.daemon.answers[f"{label_of(slot)} {voice}"] = "mismatch"
        for slot, answer in self.cut_answers.items():
            self.daemon.answers[f"cut {label_of(slot)}"] = answer
        return super().sync(title)

    def _sounds(self, path: str) -> tuple[int, ...]:
        return tuple(self.codec.sound(path, 0, second) for second in range(max(1, self.codec.seconds(path))))

    def _work(self, op_id: str, extension: str = ".mp4") -> str:
        return os.path.join(self.root, "work", hashlib.md5(op_id.encode()).hexdigest()[:12] + extension)

    async def do(self, op, results):
        if op.kind in DOWNLOADS:
            row = op.subject
            path = self._work(op.id)
            done, failure = await ytdlp.download(row.link, path, _quiet)
            if not done:
                logging.getLogger("plan_disk").warning("загрузка %s не удалась: %s", op.id, failure)
            if not done:
                if row.id in self.transient:
                    raise Passing
                return None
            if op.kind == "download_video":
                video = Video(row.id, row.snapshot(), row.slot, row.id)
                self.content[video.key] = (await asyncio.to_thread(self.codec.picture, path),)
                return Made(video, path)
            if op.kind == "download_dub":
                # A compilation as a voice-over (Д-19) holds an episode in every second.
                self.content[("dub", row.id)] = await asyncio.to_thread(self._sounds, path)
                return Made(row, path)
            return Made(None, path)
        if op.kind == "cut":
            way = op.subject
            ref2 = (self.library(way.ref2.container.name) if way.ref2.kind == "asset"
                    else results[f"row:{way.ref2.row.id}"].file)
            picture = (self.library(way.picture.name) if way.picture is not None
                       else results[f"picture:{way.row.id}"].file)
            label = f"cut {label_of(op.slot)}"
            result = await self._daemon("reverse", {"compilation": picture,
                                                    "episodes": [ref2], "out_dir": os.path.join(self.root, "work"),
                                                    "extend_edges": True, "label": label})
            if result is None:
                return None
            video = Video(way.row.id, way.snapshot, op.slot, way.timeline, cut_of=way.row.id)
            self.content[video.key] = (await asyncio.to_thread(self.codec.picture, result),)
            return Made(video, result)
        if op.kind == "align":
            goal, way = op.subject
            video_path = self.files.video_path(goal, results)
            if way.kind == "download":
                dub, stream = results[f"dub:{way.dub}"].file, 0
            else:
                dub, stream = self.library(way.container.name), await self.files.stream_of(way.container, way.dub, way.own)
            label = f"{label_of(goal.slot)} {way.voice}"
            result = await self._daemon("enqueue", {"ref": video_path, "dubs": [dub], "dub_atracks": [stream],
                                                    "out_dir": os.path.join(self.root, "work"), "label": label})
            if result is None:
                return None
            return Made(Track(way.dub, way.snapshot, goal.slot, video_of(goal, results).timeline), result)
        if op.kind == "build":
            return Made(op.subject)
        raise AssertionError(op.kind)

    async def _daemon(self, route: str, body: dict) -> str | None:
        """The service's own client to the model of the service; the path of the result, or None when it failed."""
        client = self.daemon.client()
        try:
            answer = await (client.enqueue(body) if route == "enqueue" else client.reverse(body))
            job = answer if isinstance(answer, dict) else answer[0]
            job = await client.job(job["id"])
        except Unconfirmed:
            raise Passing from None  # the answer never came: the job is looked at again at the next sync
        finally:
            await client.close()
        # The service's answer as the conform module reads it: the verdict is the product's own reading of it.
        results = finished_view(job, {})["results"]
        return judged(results[0] if results else None, cut=route == "reverse")

    def aligned_truth(self, aligned: Made) -> tuple[int, ...]:
        return (self.codec.sound(aligned.file, 0),)

    async def stage(self, made, group, results) -> list:
        await World.stage(self, made, group, results)
        return await self.files.build(made, group, results)

    async def commit(self, before, staged=None) -> None:
        if self.crash_at == -1:
            raise Crash("before the journal")
        table = [container_json(each) for each in sorted(self.containers.values(), key=lambda each: each.name)]
        if not staged:
            # The end of the sync, or a group that moves no file: the table as it is now, what was made aside goes.
            if staged is None:
                self.files.sweep()
                self._clean()
            with open(self.table, "w") as out:
                json.dump(table, out)
            return
        steps = self.files.write_journal(staged, table)
        await self._carry_out(steps, table)

    async def _carry_out(self, steps: list, table: list) -> None:
        by_name = {each["name"]: each for each in table}
        for number, step in enumerate(steps):
            if self.crash_at == number:
                raise Crash(f"at step {number}: {step}")
            await self.files.step(step, by_name)
        with open(self.table, "w") as out:
            json.dump(table, out)
        self.files.finish()

    def recover(self) -> None:
        """The restart after a crash: a journal is carried out to the end; with no journal the snapshot stands."""
        self.crash_at = None
        journal = self.files.read_journal()
        if journal is not None:
            asyncio.run(self._carry_out(journal["steps"], journal["table"]))
        self.files.sweep()
        self._clean()
        with open(self.table) as found:
            self.containers = {each["name"]: container_of(each) for each in json.load(found)}

    def _clean(self) -> None:
        shutil.rmtree(os.path.join(self.root, "work"), ignore_errors=True)
        os.makedirs(os.path.join(self.root, "work"), exist_ok=True)

    def _write_table(self) -> None:
        with open(self.table, "w") as out:
            json.dump([container_json(each) for each in self.containers.values()], out)

    # --- the checks of the disk --------------------------------------------------------------------------------

    def check(self, step=None) -> None:
        super().check(step)
        self.check_disk()

    def check_disk(self) -> None:
        """The disk alone: the files are the table, each holds one episode and carries its provenance; adoption."""
        files = self.files.files()
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
            # Every sound bears the name its table gives its group (the user's name, else the group's own).
            titles = dict(self.containers[name].titles)
            assert [track.title for track in held.audio] == [titles.get(track.label, track.label) for track in held.audio], \
                f"{name}: the titles of its sounds are not the table's"
            # Every sound bears the language its table gives its group; one the user gave none claims none.
            languages = dict(self.containers[name].languages)
            assert [track.language for track in held.audio] == [languages.get(track.label, mkv.UNSTATED)
                                                                for track in held.audio], \
                f"{name}: the languages of its sounds are not the table's"
            provenance = asyncio.run(mkv.provenance_of(path))
            assert provenance == container_json(self.containers[name]), f"{name}: its provenance is not the table"
            adopted.append(container_of(provenance))
        # Adoption: the asset told again from the files alone is the table.
        assert {each.name: each for each in adopted} == self.containers, "the files do not tell the table"

    def close(self) -> None:
        self.host.close()


async def _quiet(_data) -> None:
    return None
