"""Command-line entry point for the Vibix stand."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx

from .capture import VibixUnavailable, capture_playlist
from .download import HEADERS, download_many
from .keys import KeyStore
from .protocol import (VibixProtocolError, decode_payload, parse_all_eps,
                       parse_playlist)

_EPISODE_IN_URL = re.compile(r"/episode-(\d+)")


def _release_url(url: str) -> str:
    match = re.search(r"(https?://[^/]+/release/[^/?#]+)", url)
    return match.group(1) if match else url


def _episode_from_url(url: str) -> str | None:
    match = _EPISODE_IN_URL.search(url)
    return match.group(1) if match else None


def _fetch_release(url: str) -> tuple[str, list[dict]]:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
        response = client.get(_release_url(url))
        response.raise_for_status()
    return response.text, parse_all_eps(response.text)


def command_list(args: argparse.Namespace) -> int:
    html, episodes = _fetch_release(args.url)
    title = "release"
    match = re.search(r'"name":"([^"]+)"', html)
    if match:
        title = match.group(1)
    print(f"{title}: {len(episodes)} серий")
    for episode in episodes:
        number = episode.get("number")
        date = episode.get("date") or ""
        name = episode.get("title") or f"Серия {number}"
        print(f"{str(number):>4}  {date:<12}  {name}")
    return 0


def command_probe(args: argparse.Namespace) -> int:
    page_url = args.url
    if _episode_from_url(page_url) is None:
        page_url = page_url.rstrip("/") + "/episode-1"
    capture = asyncio.run(capture_playlist(page_url, browser_name=args.browser,
                                           timeout_s=args.timeout))
    store = KeyStore()
    key, refreshed = store.resolve(api_body=capture.body,
                                   manifest_body=capture.manifest_body)
    payload = decode_payload(capture.body, key)
    episodes = parse_playlist(payload)
    print(f"API: {capture.api_url}")
    print(f"Ключ: {'восстановлен' if refreshed else 'из кэша'} ({store.path})")
    print(f"Серий в плейлисте: {len(episodes)}")
    if episodes:
        first, last = episodes[0], episodes[-1]
        print(f"Первая: {first.title}; последняя: {last.title}")
        qualities = sorted({stream.quality for episode in episodes
                            for stream in episode.streams}, reverse=True)
        print(f"Качества: {', '.join(f'{quality}p' for quality in qualities)}")
        for episode in (first, last):
            stream = episode.pick_stream(args.quality)
            print(f"  {episode.position}: {stream.quality}p {stream.url}")
    if args.save_dir:
        directory = Path(args.save_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "api.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "episodes.json").write_text(
            json.dumps([{"position": episode.position, "title": episode.title,
                         "season": episode.season,
                         "streams": [{"quality": stream.quality, "url": stream.url}
                                     for stream in episode.streams]}
                        for episode in episodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Сохранено: {directory}")
    return 0


def command_download(args: argparse.Namespace) -> int:
    selection = args.episodes or _episode_from_url(args.url)
    if not selection:
        print("Укажите --episodes (например, 387 или 1-10); в ссылке нет номера серии.",
              file=sys.stderr)
        return 2

    def progress(number: int, status: str) -> None:
        print(f"{number:04d}: {status}", flush=True)

    try:
        downloaded, skipped, failed = asyncio.run(download_many(
            episode_url=args.url, selection=selection, quality=args.quality,
            out_dir=Path(args.out), jobs=args.jobs, skip_existing=args.skip_existing,
            max_seconds=args.max_seconds, keep_manifests=args.keep_manifests,
            browser_name=args.browser, progress=progress,
        ))
    except (VibixUnavailable, VibixProtocolError, httpx.HTTPError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Готово: скачано {downloaded}, пропущено {skipped}, ошибок {len(failed)}")
    for item in failed:
        print(f"  {item}", file=sys.stderr)
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibix-stand",
        description="Стенд Vibix: список серий, плейлист и скачивание через HLS.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="показать серии со страницы релиза")
    list_parser.add_argument("url")
    list_parser.set_defaults(func=command_list)

    probe_parser = subparsers.add_parser("probe", help="получить плейлист Vibix через браузер")
    probe_parser.add_argument("url")
    probe_parser.add_argument("--quality", type=int, default=1080, choices=(480, 720, 1080))
    probe_parser.add_argument("--browser", default="firefox", choices=("firefox", "chromium"))
    probe_parser.add_argument("--timeout", type=float, default=60.0)
    probe_parser.add_argument("--save-dir")
    probe_parser.set_defaults(func=command_probe)

    download_parser = subparsers.add_parser("download", help="скачать серии через ffmpeg")
    download_parser.add_argument("url")
    download_parser.add_argument("--episodes", help="all, 387, 1-10, 1,5,7-9")
    download_parser.add_argument("--quality", type=int, default=1080, choices=(480, 720, 1080))
    download_parser.add_argument("--out", default="vibix-downloads")
    download_parser.add_argument("--jobs", type=int, default=2)
    download_parser.add_argument("--browser", default="firefox", choices=("firefox", "chromium"))
    download_parser.add_argument("--skip-existing", action="store_true")
    download_parser.add_argument("--max-seconds", type=float, help="ограничить длительность (отладка)")
    download_parser.add_argument("--keep-manifests", action="store_true")
    download_parser.set_defaults(func=command_download)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        return 0
    except (VibixUnavailable, VibixProtocolError, httpx.HTTPError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
