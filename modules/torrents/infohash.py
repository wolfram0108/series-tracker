"""Локальное вычисление infohash торрента (решение Р-2).

Infohash v1 — SHA1 байтов секции `info` .torrent-файла; его qBittorrent
использует как первичный ключ торрента. Считаем по исходным байтам
файла (span-подход), а не пересборкой словаря: так результат не зависит
от каких-либо допущений о канонической сортировке ключей.

Magnet: hash записан в самой ссылке (`xt=urn:btih:...`) в hex (40
символов) или base32 (32 символа). Новый формат v2 (`urn:btmh:`)
осознанно не поддерживается — явная ошибка вместо тихой (в рунете
v2-магниты пока экзотика; см. contracts/revision.md Р-2).
"""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse


class TorrentParseError(ValueError):
    """Невалидный .torrent / magnet или неподдерживаемый формат."""


# --- bencode: декодер со спанами -------------------------------------------

def _decode(data: bytes, pos: int) -> tuple[object, int]:
    """Декодирует значение с позиции pos, возвращает (значение, конец)."""
    if pos >= len(data):
        raise TorrentParseError("неожиданный конец данных")
    ch = data[pos:pos + 1]
    if ch == b"i":  # целое: i<число>e
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if ch == b"l":  # список: l<значения>e
        items, pos = [], pos + 1
        while data[pos:pos + 1] != b"e":
            item, pos = _decode(data, pos)
            items.append(item)
        return items, pos + 1
    if ch == b"d":  # словарь: d<ключ><значение>...e
        result, pos = {}, pos + 1
        while data[pos:pos + 1] != b"e":
            key, pos = _decode(data, pos)
            if not isinstance(key, bytes):
                raise TorrentParseError("ключ словаря — не строка")
            value_start = pos
            value, pos = _decode(data, pos)
            result[key] = (value, value_start, pos)  # значение + спан
        return result, pos + 1
    if ch.isdigit():  # байтовая строка: <длина>:<байты>
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        if start + length > len(data):
            raise TorrentParseError("строка выходит за пределы данных")
        return data[start:start + length], start + length
    raise TorrentParseError(f"неизвестный маркер bencode: {ch!r}")


def infohash_candidates(content: bytes) -> list[str]:
    """Кандидаты infohash (hex, нижний регистр) из .torrent-файла.

    Классический торрент → один кандидат: SHA1 секции info (v1).
    Гибрид/v2 (`meta version: 2`) → ДВА кандидата, первым — укороченный
    SHA256: libtorrent 2.x (qBittorrent ≥4.4) ключует гибриды именно им.
    Подтверждено на данных прода: раздача с meta version 2 лежала в БД
    под sha256(info)[:40], остальные 169 — под sha1 (см. revision.md Р-2).
    Какой кандидат реально использует qBit — определяется контрольным
    запросом после добавления.
    """
    try:
        top, _ = _decode(content, 0)
    except (ValueError, IndexError) as exc:
        raise TorrentParseError(f"не удалось разобрать bencode: {exc}") from exc
    if not isinstance(top, dict) or b"info" not in top:
        raise TorrentParseError("в .torrent отсутствует секция info")
    info, start, end = top[b"info"]
    info_bytes = content[start:end]
    v1 = hashlib.sha1(info_bytes).hexdigest()
    meta_version = info.get(b"meta version", (None,))[0]
    if meta_version == 2:
        v2_truncated = hashlib.sha256(info_bytes).hexdigest()[:40]
        return [v2_truncated, v1]
    return [v1]


# --- magnet ------------------------------------------------------------------

def infohash_from_magnet(link: str) -> str:
    """Infohash v1 (hex, нижний регистр) из magnet-ссылки."""
    parsed = urlparse(link)
    if parsed.scheme != "magnet":
        raise TorrentParseError("не magnet-ссылка")
    for xt in parse_qs(parsed.query).get("xt", []):
        if xt.startswith("urn:btmh:"):
            raise TorrentParseError(
                "magnet формата BitTorrent v2 (btmh) не поддерживается")
        if not xt.startswith("urn:btih:"):
            continue
        value = xt[len("urn:btih:"):]
        if len(value) == 40:  # hex
            try:
                bytes.fromhex(value)
            except ValueError as exc:
                raise TorrentParseError(f"невалидный hex-hash: {value}") from exc
            return value.lower()
        if len(value) == 32:  # base32 (старые ссылки)
            try:
                raw = base64.b32decode(value.upper())
            except Exception as exc:
                raise TorrentParseError(f"невалидный base32-hash: {value}") from exc
            return raw.hex()
        raise TorrentParseError(f"hash неожиданной длины {len(value)}: {value}")
    raise TorrentParseError("в magnet-ссылке нет urn:btih")
