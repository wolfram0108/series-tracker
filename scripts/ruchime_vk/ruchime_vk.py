#!/usr/bin/env python3
"""Fetch an episode of 1.ruchime.org through its VK player.

Chain: episode page -> VK embed (video_ext.php) -> video item -> mp4_<height> files -> download.
The embed comes in two page variants; the old one carries the video item in the HTML,
the new one needs an anonymous token and one API call. Stdlib only.
"""

import argparse
import html
import http.client
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Version the embed page sends with its own API calls.
API_VERSION = "5.289"
API_URL = "https://api.vk.ru/method/video.getEmbed"
EMBED_ORIGIN = "https://vkvideo.ru"
# Without these the embed sometimes answers with an auto-login redirect and then 400.
PRESET_COOKIES = {"_ignoreAutoLogin": "1", "remix_isCookieIgnoreAutologinMigrated": "1"}
TIMEOUT_SECONDS = 30
ATTEMPTS = 3
DOWNLOAD_ATTEMPTS = 3
CHUNK_SIZE = 1024 * 1024

EPISODE_URL_RE = re.compile(
    r"^https?://(?:[\w-]+\.)*ruchime\.org/release/(?P<slug>[^/?#]+)/episode-(?P<number>\d+)"
)
EMBED_URL_RE = re.compile(r"https?://(?:vkvideo\.ru|vk\.com)/video_ext\.php\?[^\"'\s<>)\\]+")
# The token sits inline in the embed HTML: webToken: {"access_token":"anonym...."}.
TOKEN_RE = re.compile(r'webToken:\s*\{\s*"access_token"\s*:\s*"([^"]+)"')
# Old page variant: the video item is embedded as JSON right after this marker.
OLD_ITEM_RE = re.compile(r'"response":\{"count":1,"items":\[')
RESTRICTED_TEXT = "скрыто настройками приватности"
MP4_KEY_RE = re.compile(r"^mp4_(\d+)$")


class RuchimeVkError(Exception):
    """A failure whose message is shown to the user as is."""


class TransientError(RuchimeVkError):
    """A failure a fresh session may not repeat; resolve() retries these."""


@dataclass(frozen=True)
class EmbedRef:
    url: str
    owner_id: str
    video_id: str
    access_hash: str


@dataclass(frozen=True)
class Rendition:
    height: int
    url: str


@dataclass(frozen=True)
class VkVideo:
    title: str
    duration: int
    embed: EmbedRef
    renditions: list[Rendition]


def _cookie(name, value):
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain="vkvideo.ru", domain_specified=True, domain_initial_dot=False,
        path="/", path_specified=True, secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )


def _new_opener():
    # One cookie jar per attempt, so a poisoned session cannot leak into the next try.
    jar = http.cookiejar.CookieJar()
    for name, value in PRESET_COOKIES.items():
        jar.set_cookie(_cookie(name, value))
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _open(opener, request):
    try:
        return opener.open(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuchimeVkError(f"страница не найдена: {request.full_url}") from e
        raise TransientError(f"HTTP {e.code} for {request.full_url}") from e
    except (OSError, http.client.HTTPException) as e:
        raise TransientError(f"network error for {request.full_url}: {e}") from e


def _headers(referer=None, **extra):
    headers = {"User-Agent": USER_AGENT, **extra}
    if referer:
        headers["Referer"] = referer
    return headers


def get_text(opener, url, referer=None):
    with _open(opener, urllib.request.Request(url, headers=_headers(referer))) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    # The old embed page is served as windows-1251; decoding it as UTF-8 garbles the Russian text.
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def parse_episode_url(episode_url):
    match = EPISODE_URL_RE.match(episode_url)
    if match is None:
        raise RuchimeVkError(f"нужна ссылка вида .../release/<slug>/episode-<N>, получено: {episode_url}")
    return match


def find_embed_url(episode_html):
    # The page carries the same VK embed in an iframe and in the "VK Video" source button.
    match = EMBED_URL_RE.search(episode_html)
    if match is None:
        raise RuchimeVkError("на странице серии нет плеера VK")
    return html.unescape(match.group(0))


def parse_embed_url(url):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    try:
        owner_id = query["oid"][0]
        video_id = query["id"][0]
        access_hash = query["hash"][0]
    except (KeyError, IndexError) as e:
        raise RuchimeVkError(f"в ссылке плеера нет oid, id или hash: {url}") from e
    if not (re.fullmatch(r"-?\d+", owner_id) and re.fullmatch(r"\d+", video_id) and re.fullmatch(r"\w+", access_hash)):
        raise RuchimeVkError(f"непонятные oid/id/hash в ссылке плеера: {url}")
    return EmbedRef(url=url, owner_id=owner_id, video_id=video_id, access_hash=access_hash)


def video_from_old_page(embed_html):
    match = OLD_ITEM_RE.search(embed_html)
    if match is None:
        return None
    try:
        items, _ = json.JSONDecoder().raw_decode(embed_html, match.end() - 1)
    except ValueError:
        return None
    return items[0] if items else None


def find_anonymous_token(embed_html):
    match = TOKEN_RE.search(embed_html)
    if match is None:
        raise TransientError("в HTML плеера нет ни данных видео, ни анонимного токена: страница в неизвестном виде")
    return match.group(1)


def fetch_video(opener, embed, token, episode_url):
    body = urllib.parse.urlencode({
        "owner_id": embed.owner_id,
        "video_id": embed.video_id,
        "hash": embed.access_hash,
        "ref_url": episode_url,
        "access_token": token,
        "v": API_VERSION,
    }).encode()
    request = urllib.request.Request(API_URL, data=body, headers=_headers(embed.url, Origin=EMBED_ORIGIN))
    with _open(opener, request) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        error = payload["error"]
        raise RuchimeVkError(f"VK API: {error.get('error_msg', 'ошибка')} (код {error.get('error_code')})")
    video = (payload.get("response") or {}).get("video")
    if not video:
        raise RuchimeVkError("VK не вернул описание видео")
    return video


def list_renditions(video):
    renditions = []
    for key, url in (video.get("files") or {}).items():
        match = MP4_KEY_RE.match(key)
        if match and url:
            renditions.append(Rendition(height=int(match.group(1)), url=url))
    return sorted(renditions, key=lambda r: r.height, reverse=True)


def resolve_once(episode_url, opener):
    embed = parse_embed_url(find_embed_url(get_text(opener, episode_url)))
    embed_html = get_text(opener, embed.url, referer=episode_url)
    video = video_from_old_page(embed_html)
    if video is None and not TOKEN_RE.search(embed_html):
        # A closed video gets a plain page with this sentence; retrying would not change it.
        if RESTRICTED_TEXT in embed_html:
            raise RuchimeVkError("видео скрыто настройками приватности и недоступно без входа в VK")
    if video is None:
        token = find_anonymous_token(embed_html)
        video = fetch_video(opener, embed, token, episode_url)
    renditions = list_renditions(video)
    if not renditions:
        raise RuchimeVkError("VK не отдал mp4-файлы; остался только HLS/DASH, он в этой версии не поддержан")
    return VkVideo(
        title=video.get("title") or "",
        duration=int(video.get("duration") or 0),
        embed=embed,
        renditions=renditions,
    )


def resolve(episode_url):
    parse_episode_url(episode_url)
    last_error = None
    for _ in range(ATTEMPTS):
        try:
            return resolve_once(episode_url, _new_opener())
        except TransientError as e:
            last_error = e
    raise RuchimeVkError(f"не удалось открыть плеер (попыток: {ATTEMPTS}): {last_error}")


def pick_rendition(renditions, max_height):
    candidates = [r for r in renditions if max_height is None or r.height <= max_height]
    if not candidates:
        raise RuchimeVkError(f"нет mp4 с высотой не выше {max_height}p; доступны: {[r.height for r in renditions]}")
    return candidates[0]


def download(url, referer, destination):
    # Write to .part and rename at the end, so a cut-off file never looks finished.
    part = destination.with_name(destination.name + ".part")
    opener = urllib.request.build_opener()
    last_error = None
    for _ in range(DOWNLOAD_ATTEMPTS):
        try:
            with _open(opener, urllib.request.Request(url, headers=_headers(referer))) as response:
                expected = response.headers.get("Content-Length")
                written = 0
                with part.open("wb") as out:
                    for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                        out.write(chunk)
                        written += len(chunk)
            # A dropped connection can end the body early without an error; compare with the declared size.
            if expected is not None and written != int(expected):
                raise RuchimeVkError(f"скачано {written} из {expected} байт")
            part.replace(destination)
            return written
        except (RuchimeVkError, OSError, http.client.HTTPException) as e:
            last_error = e
            part.unlink(missing_ok=True)
    raise RuchimeVkError(f"не удалось скачать (попыток: {DOWNLOAD_ATTEMPTS}): {last_error}")


def default_name(episode_url, height):
    match = parse_episode_url(episode_url)
    return f"{match['slug']}-episode-{match['number']}-{height}p.mp4"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Скачать серию 1.ruchime.org с плеера VK.")
    parser.add_argument("episode_url", help="ссылка вида https://1.ruchime.org/release/<slug>/episode-<N>")
    parser.add_argument("--out", type=Path, help="путь к файлу или существующая папка")
    parser.add_argument("--max-height", type=int, help="не брать mp4 выше этой высоты, например 1080")
    parser.add_argument("--list", action="store_true", help="показать варианты плеера и не скачивать")
    args = parser.parse_args(argv)

    try:
        video = resolve(args.episode_url)
        chosen = pick_rendition(video.renditions, args.max_height)
        report = {
            "episode_url": args.episode_url,
            "vk_embed": video.embed.url,
            "vk_video": f"{video.embed.owner_id}_{video.embed.video_id}",
            "title": video.title,
            "duration_s": video.duration,
            "renditions": [r.height for r in video.renditions],
            "chosen_height": chosen.height,
        }
        if not args.list:
            name = default_name(args.episode_url, chosen.height)
            destination = args.out / name if args.out and args.out.is_dir() else (args.out or Path(name))
            # Fail before the multi-hundred-megabyte request, not after it.
            if not destination.resolve().parent.is_dir():
                raise RuchimeVkError(f"папка для файла не существует: {destination.parent}")
            report["file"] = str(destination.resolve())
            report["bytes"] = download(chosen.url, video.embed.url, destination)
    except RuchimeVkError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
