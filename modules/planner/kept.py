"""The aligned sounds of the episodes whose container is not built yet: results of the alignment service that outlive its job.

The job of the service goes with its folder (a stop of the sync, the setting «clear finished», the owner's hand), and a sync that
falls before its container is put in place would lose what hours of the service's work made. So every result is also kept here, as a
hard link to the file the job wrote — never a second copy of the data — under its episode, with the timeline it was aligned to and the
row of its voice-over. A sync that asks the same alignment finds it here instead of asking the service again and downloading the
voice-over. An episode holds one set: an alignment to another timeline replaces it. The set goes once the container built of it is
in place, when a sync finds the episode needs no build, and when the series is deleted.

Layout: `<root>/<s01e02>/manifest.json` and the files beside it; `root` is of one series.
"""
from __future__ import annotations

import json
import os
import shutil

from calc.plan.tables import label_of
from core.fsnames import sanitize_name

MANIFEST = "manifest.json"
KEPT = "kept"  # beside the folders of the planner's jobs in the work folder of the alignment service


class Kept:
    """The kept alignments of one series. The calls are blocking (the folder lies on a network disk): the caller runs them in
    the storage pool. Row ids and timelines are given as plain dicts, the way the manifest holds them."""

    def __init__(self, root: str) -> None:
        self.root = root

    @classmethod
    def of(cls, conform: str, series_id: int) -> "Kept":
        """The alignments kept for a series, beside the folders of the jobs (which the sweep of the planner clears)."""
        return cls(os.path.join(conform, KEPT, str(series_id)))

    def folder(self, slot) -> str:
        return os.path.join(self.root, label_of(tuple(slot)))

    def _read(self, slot) -> dict | None:
        try:
            with open(os.path.join(self.folder(slot), MANIFEST), encoding="utf-8") as stream:
                return json.load(stream)
        except (FileNotFoundError, NotADirectoryError, ValueError):
            return None

    def _write(self, slot, data: dict) -> None:
        path = os.path.join(self.folder(slot), MANIFEST)
        with open(path + ".new", "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
        os.replace(path + ".new", path)

    def find(self, slot, timeline: dict, voice: str, dub: dict) -> str | None:
        """The file of the alignment of this voice-over of the episode to this timeline; None when it is not kept, was made
        from another row, or its file is not whole (its size is not the one written)."""
        held = self._read(slot)
        if held is None or held["timeline"] != timeline:
            return None
        for each in held["tracks"]:
            if each["voice"] == voice and each["dub"] == dub:
                path = os.path.join(self.folder(slot), each["file"])
                try:
                    return path if os.stat(path).st_size == each["size"] else None
                except OSError:
                    return None
        return None

    def keep(self, slot, timeline: dict, voice: str, dub: dict, source: str) -> str:
        """Keep the file `source` of a job as the alignment of the voice-over; the path it is kept at. A hard link: where none can
        be made the error is raised, the data is not copied."""
        held = self._read(slot)
        if held is not None and held["timeline"] != timeline:
            shutil.rmtree(self.folder(slot), ignore_errors=True)  # aligned to another video: not in step with this one
            held = None
        os.makedirs(self.folder(slot), exist_ok=True)
        target = os.path.join(self.folder(slot), sanitize_name(voice) + os.path.splitext(source)[1])
        if not (os.path.exists(target) and os.path.samefile(source, target)):
            if os.path.exists(target + ".new"):
                os.remove(target + ".new")  # left by a link a stop cut short
            os.link(source, target + ".new")
            os.replace(target + ".new", target)
        tracks = [each for each in (held or {}).get("tracks", ()) if each["voice"] != voice]
        tracks.append({"voice": voice, "dub": dub, "file": os.path.basename(target), "size": os.stat(target).st_size})
        self._write(slot, {"slot": list(slot), "timeline": timeline, "tracks": tracks})
        return target

    def retire(self, slot) -> bool:
        """The episode's set goes (its files are links: the data stays with whoever else holds it); whether there was one."""
        existed = os.path.isdir(self.folder(slot))
        shutil.rmtree(self.folder(slot), ignore_errors=True)
        try:
            os.rmdir(self.root)  # the last set of the series leaves no empty folder behind it
        except OSError:
            pass
        return existed

    def slots(self) -> list[tuple]:
        """The episodes that hold a set."""
        found = []
        for name in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else ():
            try:
                with open(os.path.join(self.root, name, MANIFEST), encoding="utf-8") as stream:
                    found.append(tuple(json.load(stream)["slot"]))
            except (FileNotFoundError, NotADirectoryError, ValueError, KeyError):
                continue
        return found

    def drop(self) -> None:
        """The whole series' sets go."""
        shutil.rmtree(self.root, ignore_errors=True)
