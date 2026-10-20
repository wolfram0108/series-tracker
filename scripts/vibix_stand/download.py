"""Resolve Vibix playlists and download episodes with ffmpeg."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import httpx

from .capture import Capture, capture_playlist
from .keys import KeyStore
from .protocol import (Episode, Stream, VibixProtocolError, decode_playlist,
                       DEFAULT_DECODER_KEY, parse_manifest, stream_expires)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                   "Gecko/20100101 Firefox/128.0"),
    "Referer": "https://1.ruchime.org/",
}
REFRESH_MARGIN_S = 600


class ManifestExpired(RuntimeError):
    """Raised when a signed manifest URL is no longer accepted."""


@dataclass
class PlaylistSession:
    episode_url: str
    browser_name: str = "firefox"
    key_store: KeyStore = field(default_factory=KeyStore)
    episodes: list[Episode] = field(default_factory=list)
    expires: int | None = None
    generation: int = 0
    api_url: str = ""
    key: bytes = b""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            now = int(time.time())
            stale = self.expires is None or self.expires - now < REFRESH_MARGIN_S
            if not force and not stale:
                return
            capture = await capture_playlist(self.episode_url, browser_name=self.browser_name)
            self._apply(capture)

    def _apply(self, capture: Capture) -> None:
        key, _ = self.key_store.resolve(api_body=capture.body,
                                        manifest_body=capture.manifest_body)
        episodes = decode_playlist(capture.body, key)
        if not episodes:
            raise VibixProtocolError("Vibix API returned an empty playlist")
        self.key = key
        self.episodes = episodes
        self.api_url = capture.api_url
        self.generation += 1
        expiries = [stream_expires(stream.url)
                    for episode in episodes for stream in episode.streams]
        expiries = [value for value in expiries if value is not None]
        self.expires = min(expiries) if expiries else int(time.time()) + 3600

    @property
    def numbers(self) -> list[int]:
        return [episode.number for episode in self.episodes]

    def by_number(self, number: int) -> Episode:
        for episode in self.episodes:
            if episode.number == number:
                return episode
        raise KeyError(f"серии {number} нет в плейлисте Vibix")

    def find(self, number: int) -> Episode | None:
        try:
            return self.by_number(number)
        except KeyError:
            return None


Progress = tuple[int, str]


async def download_many(*, episode_url: str, selection: str, quality: int | None,
                        out_dir: Path, jobs: int = 2, skip_existing: bool = False,
                        max_seconds: float | None = None, keep_manifests: bool = False,
                        browser_name: str = "firefox",
                        progress: Callable[[int, str], None] | None = None
                        ) -> tuple[int, int, list[str]]:
    session = PlaylistSession(episode_url=episode_url, browser_name=browser_name)
    await session.refresh()
    numbers = parse_selection(selection, session.numbers)
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, jobs))
    lock = asyncio.Lock()
    downloaded = 0
    skipped = 0
    failed: list[str] = []

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=60.0) as client:
        async def worker(number: int) -> None:
            nonlocal downloaded, skipped
            async with semaphore:
                try:
                    result = await download_one(
                        session=session, client=client, number=number, quality=quality,
                        out_dir=out_dir, skip_existing=skip_existing,
                        max_seconds=max_seconds, keep_manifests=keep_manifests,
                    )
                except Exception as exc:  # noqa: BLE001 - the summary must survive one bad episode
                    async with lock:
                        failed.append(f"{number}: {exc}")
                    if progress:
                        progress(number, f"ошибка: {exc}")
                    return
                async with lock:
                    if result == "пропуск":
                        skipped += 1
                    else:
                        downloaded += 1
                if progress:
                    progress(number, result)

        await asyncio.gather(*(worker(number) for number in numbers))

    return downloaded, skipped, failed


async def download_one(*, session: PlaylistSession, client: httpx.AsyncClient, number: int,
                       quality: int | None, out_dir: Path, skip_existing: bool,
                       max_seconds: float | None, keep_manifests: bool) -> str:
    await session.refresh()
    episode = session.by_number(number)
    stream = episode.pick_stream(quality)
    out_path = out_dir / f"{number:04d}.mp4"
    if skip_existing and out_path.exists():
        return "пропуск"

    manifest_text = await _fetch_manifest_with_refresh(
        session=session, client=client, number=number, quality=quality, stream=stream
    )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")

    part_path = out_path.with_name(out_path.stem + ".part.mp4")
    manifest_path = _write_manifest(manifest_text, out_dir if keep_manifests else None,
                                    number)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-protocol_whitelist", "file,http,https,tcp,tls",
        "-i", str(manifest_path),
        "-c", "copy", "-movflags", "+faststart",
    ]
    if max_seconds is not None:
        command += ["-t", str(max_seconds)]
    command.append(str(part_path))

    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip().splitlines()
            raise RuntimeError(message[-1] if message else "ffmpeg завершился с ошибкой")
        part_path.replace(out_path)
    finally:
        if part_path.exists():
            part_path.unlink(missing_ok=True)
        if not keep_manifests:
            manifest_path.unlink(missing_ok=True)

    return f"{stream.quality}p"


async def _fetch_manifest_with_refresh(*, session: PlaylistSession, client: httpx.AsyncClient,
                                       number: int, quality: int | None,
                                       stream: Stream) -> str:
    generation = session.generation
    try:
        return await fetch_manifest(client, stream.url, session.key)
    except (ManifestExpired, VibixProtocolError):
        await session.refresh(force=True)
        if session.generation == generation:
            raise
        episode = session.by_number(number)
        return await fetch_manifest(client, episode.pick_stream(quality).url, session.key)


async def fetch_manifest(client: httpx.AsyncClient, url: str,
                         key: str | bytes = DEFAULT_DECODER_KEY) -> str:
    response = await client.get(url)
    if response.status_code in (401, 403, 404, 410):
        raise ManifestExpired(f"manifest HTTP {response.status_code}")
    response.raise_for_status()
    return parse_manifest(response.content, key)


def _write_manifest(text: str, keep_dir: Path | None, number: int) -> Path:
    if keep_dir is not None:
        keep_dir.mkdir(parents=True, exist_ok=True)
        path = keep_dir / f"{number:04d}.m3u8"
        path.write_text(text, encoding="utf-8")
        return path
    handle = tempfile.NamedTemporaryFile("w", suffix=".m3u8", delete=False, encoding="utf-8")
    with handle:
        handle.write(text)
    return Path(handle.name)


def parse_selection(spec: str, available: Iterable[int]) -> list[int]:
    """Parse `all`, `387`, `1-10,15` into a sorted list of available numbers."""
    known = sorted(set(available))
    if spec.strip().lower() == "all":
        return known
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            selected.update(range(min(start, end), max(start, end) + 1))
        else:
            selected.add(int(part))
    missing = sorted(selected.difference(known))
    if missing:
        raise ValueError(f"нет таких серий: {', '.join(map(str, missing))}")
    return sorted(selected)
