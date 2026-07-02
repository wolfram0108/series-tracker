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


# --- список файлов (предпросмотр нейминга при добавлении) --------------------

def torrent_file_list(content: bytes) -> tuple[str | None, list[dict]]:
    """Список файлов из .torrent — для предпросмотра нейминга при добавлении.

    Поверх того же span-декодера, что и infohash (сеть не нужна). Ключи
    `name.utf-8`/`path.utf-8` предпочитаются: рус-трекеры кладут основной путь
    в cp1251, а qBittorrent отдаёт именно utf-8-вариант — так предпросмотр
    совпадёт с фактическим переименованием. Многофайловый torrent →
    (имя_папки, [{path, size}]); однофайловый → (None, [{path=имя, size}]).
    """
    try:
        top, _ = _decode(content, 0)
    except (ValueError, IndexError) as exc:
        raise TorrentParseError(f"не удалось разобрать bencode: {exc}") from exc
    if not isinstance(top, dict) or b"info" not in top:
        raise TorrentParseError("в .torrent отсутствует секция info")
    info = top[b"info"][0]  # _decode хранит значение словаря как (value, start, end)

    def _val(d: dict, key: bytes):
        item = d.get(key)
        return item[0] if item is not None else None

    def _text(b) -> str:
        return (b.decode("utf-8", "replace")
                if isinstance(b, (bytes, bytearray)) else str(b))

    name = _val(info, b"name.utf-8") or _val(info, b"name")
    files_raw = _val(info, b"files")
    if files_raw is not None:  # многофайловый
        files = []
        for entry in files_raw:  # элементы списка — «сырые» словари со спанами
            if not isinstance(entry, dict):
                continue
            parts = _val(entry, b"path.utf-8") or _val(entry, b"path") or []
            files.append({"path": "/".join(_text(p) for p in parts),
                          "size": _val(entry, b"length")})
        return (_text(name) if name is not None else None), files
    return None, [{"path": _text(name) if name is not None else "",
                   "size": _val(info, b"length")}]


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
