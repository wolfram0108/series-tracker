"""Тесты единой доставки sources (находка 68).

Проверяется класс, а не случай: доставка обязана переезжать на живой
хост на ЛЮБОМ пути (страница, .torrent, API) и обязана проверять, что
приехало ожидаемое — заглушка сайта с кодом 200 ответом не считается.
Ссылка раздачи при этом не меняется: она идентичность (torrent_id, Р-10).
"""
import base64
import json
from types import SimpleNamespace

import pytest

from core import Bus, ids
from modules.sources import hosts, parsers
from modules.sources.module import SourcesModule

# Minimal valid metainfo: a bencoded dict carrying an 'info' key.
TORRENT = b"d8:announce15:http://tr/annce4:infod4:name4:fileee"
SITE_STUB = ("<html><body>Мы просим Вас проявить терпение на проекте"
             "<br>ведутся технические работы</body></html>")
KINOZAL = {"canonical_name": "kinozal",
           "mirrors": ["kinozal.me", "kinozal.guru", "kinozal.tv"]}


def _module(tmp_path=None, tracker=KINOZAL, monkeypatch=None):
    mod = SourcesModule(Bus(), None,
                        torrent_cache_dir=str(tmp_path) if tmp_path else ".")

    async def resolve(url):
        return tracker

    monkeypatch.setattr(mod.repo, "resolve", resolve)
    return mod


def _env(url, torrent_id="t1"):
    return SimpleNamespace(payload={"url": url, "torrent_id": torrent_id})


def _reply(status, *, text=None, content=None):
    out = {"status": status, "final_url": "x", "content_type": "text/html"}
    if content is not None:
        out["content_type"] = "application/x-bittorrent"
        out["content_b64"] = base64.b64encode(content).decode()
    else:
        out["text"] = text or ""
    return out


# --- порядок кандидатов ------------------------------------------------------------

def test_candidates_put_canonical_host_first_and_keep_historical_last():
    assert hosts.candidates("https://dl.kinozal.guru/download.php?id=7",
                            KINOZAL["mirrors"]) == [
        "https://kinozal.guru/download.php?id=7",   # канонический — первым
        "https://kinozal.me/download.php?id=7",
        "https://kinozal.tv/download.php?id=7",
        "https://dl.kinozal.guru/download.php?id=7",  # как в ссылке — последним
    ]


def test_canonical_host_strips_delivery_prefixes_only():
    assert hosts.canonical_host("dl.kinozal.me") == "kinozal.me"
    assert hosts.canonical_host("www.rutracker.org") == "rutracker.org"
    assert hosts.canonical_host("anilibria.top") == "anilibria.top"


# --- .torrent: переезд с мёртвого хоста доставки -----------------------------------

@pytest.mark.asyncio
async def test_torrent_file_moves_off_dead_delivery_host(tmp_path, monkeypatch):
    """Мёртвый dl.-поддомен не означает отказ: файл берётся с хоста,
    который его отдаёт (симптом 12.10.2026 — 520 на dl.kinozal.guru)."""
    mod = _module(tmp_path, monkeypatch=monkeypatch)
    seen = []

    async def transport(topic, payload, **kw):
        seen.append(payload["url"])
        if payload["url"].startswith("https://kinozal.guru/"):
            return _reply(200, content=TORRENT)
        return _reply(520, text="<html>520 origin error</html>")

    monkeypatch.setattr(mod, "request", transport)
    link = "https://dl.kinozal.guru/download.php?id=7"
    reply = await mod.on_torrent_file(_env(link))

    assert base64.b64decode(reply["content_b64"]) == TORRENT
    assert seen[0] == "https://kinozal.guru/download.php?id=7"


@pytest.mark.asyncio
async def test_torrent_delivery_keeps_link_identity(tmp_path, monkeypatch):
    """Переезд доставки не меняет ссылку, а значит и torrent_id: иначе
    раздача выглядела бы новой и качалась заново (Р-10, находка 13)."""
    mod = _module(tmp_path, monkeypatch=monkeypatch)
    link = "https://dl.kinozal.me/download.php?id=7"
    before = ids.torrent_id(link, "01.01.2026 00:00:00")

    async def transport(topic, payload, **kw):
        if payload["url"].startswith("https://kinozal.tv/"):
            return _reply(200, content=TORRENT)
        return _reply(520)

    monkeypatch.setattr(mod, "request", transport)
    await mod.on_torrent_file(_env(link, torrent_id=before))

    assert ids.torrent_id(link, "01.01.2026 00:00:00") == before
    assert (tmp_path / f"{before}.torrent").read_bytes() == TORRENT


# --- приёмка результата ------------------------------------------------------------

@pytest.mark.asyncio
async def test_site_stub_with_status_200_is_not_accepted_as_file(tmp_path,
                                                                 monkeypatch):
    """Заглушка «ведутся технические работы» приезжает с кодом 200 —
    признаком успеха считается bencode, а не отсутствие HTML."""
    mod = _module(tmp_path, monkeypatch=monkeypatch)
    seen = []

    async def transport(topic, payload, **kw):
        seen.append(payload["url"])
        return _reply(200, text=SITE_STUB)

    monkeypatch.setattr(mod, "request", transport)
    with pytest.raises(parsers.SourceParseError):
        await mod.on_torrent_file(_env("https://dl.kinozal.me/download.php?id=7"))

    assert len(seen) == 4  # перебрали все хосты, ни один не дал файл


@pytest.mark.asyncio
async def test_daily_limit_stops_the_search(tmp_path, monkeypatch):
    """Суточный лимит — отказ на учётку: другие хосты его не изменят,
    перебор прекращается на первом же ответе."""
    mod = _module(tmp_path, monkeypatch=monkeypatch)
    seen = []

    async def transport(topic, payload, **kw):
        seen.append(payload["url"])
        return _reply(200, text="Вы использовали доступное Вам количество "
                                "торрент-файлов на сегодня")

    monkeypatch.setattr(mod, "request", transport)
    with pytest.raises(parsers.SourceRefusedError):
        await mod.on_torrent_file(_env("https://dl.kinozal.me/download.php?id=7"))

    assert len(seen) == 1


# --- API anilibria: зеркала больше не лежат мёртвым грузом -------------------------

@pytest.mark.asyncio
async def test_anilibria_api_falls_back_to_mirror(monkeypatch):
    """У anilibria зеркала объявлены в БД — доставка API обязана их
    использовать, как и доставка страниц."""
    mod = SourcesModule(Bus(), None)
    calls = []

    class Resp:
        def __init__(self, ok):
            self._ok = ok
            self.headers = {"content-type": "application/json"}
            self.content = json.dumps(
                {"name": {"main": "Кусуриях"}, "torrents": []}).encode()

        def raise_for_status(self):
            if not self._ok:
                raise RuntimeError("зеркало недоступно")

    class Http:
        async def get(self, url):
            calls.append(url)
            return Resp(ok="aniliberty.top" in url)

    mod._http = Http()
    res = await mod._parse_by_service(
        "anilibria", "https://anilibria.top/release/kusuriya",
        ["anilibria.top", "aniliberty.top"])

    assert len(calls) == 2  # первое зеркало отпало, взяли следующее
    assert res["title"]["ru"] == "Кусуриях"
