"""Тесты sources: разбор реальных фикстур (дамп Kinozal, JSON API
Anilibria), нормализация дат с историческими форматами, резолв зеркал.
Живые трекеры — этап 6.
"""
import json
import os
from datetime import datetime

import pytest

from core import Bus
from modules.sources import SourcesModule, anilibria, dates, parsers

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sources")


# --- даты: исторические форматы (находка 13) -------------------------------------

def test_kinozal_date_absolute():
    assert dates.kinozal_date("5 мая 2026 в 10:15") == "05.05.2026 10:15:00"


def test_kinozal_date_relative():
    now = datetime(2026, 6, 11, 12, 0, tzinfo=dates.MOSCOW_TZ)
    assert dates.kinozal_date("сегодня в 09:30", now=now) == "11.06.2026 09:30:00"
    assert dates.kinozal_date("вчера в 23:59", now=now) == "10.06.2026 23:59:00"


def test_rutracker_date():
    assert dates.rutracker_date("04-Ноя-25 10:18") == "04.11.2025 10:18:00"
    assert dates.rutracker_date("15-Мар-26 00:37") == "15.03.2026 00:37:00"


def test_anilibria_date_keeps_z_format():
    assert dates.anilibria_date("2026-03-17T20:13:16+00:00") == \
        "2026-03-17T20:13:16Z"


def test_astar_date():
    assert dates.astar_date("05-03-2026") == "05.03.2026"


def test_broken_dates_raise():
    with pytest.raises(dates.DateParseError):
        dates.kinozal_date("когда-то давно")
    with pytest.raises(dates.DateParseError):
        dates.rutracker_date("32-Xyz-99")


# --- Kinozal: реальный дамп прода --------------------------------------------------

def test_kinozal_parse_real_dump():
    html = open(os.path.join(FIX, "kinozal_page.html"), encoding="utf-8").read()
    result = parsers.kinozal_parse(html, "https://kinozal.me/details.php?id=1")
    assert result["title"]["ru"], "название не извлечено"
    (release,) = result["releases"]
    assert release["link"].startswith("https://dl.kinozal.me/download.php?id=")
    # маркер — в историческом формате Kinozal
    assert __import__("re").match(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$",
                                  release["date_marker"])


def test_kinozal_parse_garbage_raises():
    with pytest.raises(parsers.SourceParseError, match="дата"):
        parsers.kinozal_parse("<html><title>x</title></html>",
                              "https://kinozal.me/details.php?id=1")


# --- RuTracker: гостевая страница (негативный сценарий — находка 11) ---------------

def test_rutracker_guest_page_fails_loudly():
    html = open(os.path.join(FIX, "rutracker_page.html"),
                encoding="utf-8").read()
    # гостям не видна дата регистрации — парсер обязан сказать об этом
    # явно, а не выдать первую попавшуюся «похожую на дату» строку
    with pytest.raises(parsers.SourceParseError, match="нужен логин"):
        parsers.rutracker_parse(html, "https://rutracker.org/forum/viewtopic.php?t=1")


# --- Anilibria: реальный ответ API --------------------------------------------------

def test_anilibria_release_from_real_api_fixture():
    data = json.load(open(os.path.join(FIX, "anilibria_release.json"),
                          encoding="utf-8"))
    result = anilibria.release_to_result(data)
    assert result["title"]["ru"] and result["title"]["en"]
    assert len(result["releases"]) >= 1
    for r in result["releases"]:
        assert r["magnet"].startswith("magnet:")
        assert len(r["hash"]) == 40        # infohash готовым полем (Р-2)
        assert r["date_marker"].endswith("Z")  # исторический формат
        assert r["episodes"]


def test_anilibria_alias_from_url():
    assert anilibria.alias_from_url(
        "https://aniliberty.top/anime/releases/release/yu-ling-shi/torrents"
    ) == "yu-ling-shi"
    assert anilibria.alias_from_url(
        "https://anilibria.top/release/kusuriya-2") == "kusuriya-2"
    with pytest.raises(parsers.SourceParseError):
        anilibria.alias_from_url("https://example.com/нет/релиза")


# --- браузерная доставка astar/anilibria_tv (Р-9, этап 6) ------------------------

@pytest.mark.asyncio
async def test_astar_goes_through_browser_delivery(monkeypatch):
    """astar доставляется браузерным пулом (fetcher), не trackerauth;
    parse получает доставленный HTML, результат несёт фактический URL."""
    mod = SourcesModule(Bus(), None)
    captured = {}

    async def fake_html(service, url):
        captured["service"] = service
        captured["url"] = url
        return "<html>astar</html>"

    monkeypatch.setattr(mod, "_browser_html", fake_html)
    monkeypatch.setattr(parsers, "astar_parse",
                        lambda html, url: {"title": "T", "releases": [{"link": "L"}]})

    res = await mod._parse_by_service("astar", "https://astar.bz/v/1", [])
    assert captured == {"service": "astar", "url": "https://astar.bz/v/1"}
    assert res["releases"] == [{"link": "L"}]
    assert res["url"] == "https://astar.bz/v/1"


@pytest.mark.asyncio
async def test_anilibria_tv_browser_mirror_fallback(monkeypatch):
    """Перебор зеркал работает и для браузерной доставки: упавшее зеркало
    пропускается, страница берётся со следующего."""
    mod = SourcesModule(Bus(), None)
    calls = []

    async def fake_html(service, url):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("первое зеркало недоступно")
        return "<html>ok</html>"

    monkeypatch.setattr(mod, "_browser_html", fake_html)
    monkeypatch.setattr(parsers, "anilibria_tv_parse",
                        lambda html, url: {"title": "x", "releases": []})

    res = await mod._parse_by_service(
        "anilibria_tv", "https://anilibria.tv/r/1", ["mirror.anilibria.tv"])
    assert len(calls) == 2  # перешли на зеркало
    assert "mirror.anilibria.tv" in res["url"]
