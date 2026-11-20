"""Тесты sources: разбор синтетических ответов устройства настоящих (страница Kinozal, гостевая страница RuTracker,
JSON API Anilibria — `tests/samples/trackers`), нормализация дат с историческими форматами, резолв зеркал.
Живые трекеры — этап 6.
"""
import os

import pytest

from core import Bus
from modules.sources import SourcesModule, dates, parsers

FIX = os.path.join(os.path.dirname(__file__), "samples", "trackers")


# --- даты: исторические форматы (находка 13) -------------------------------------

def test_rutracker_date():
    assert dates.rutracker_date("04-Ноя-25 10:18") == "04.11.2025 10:18:00"
    assert dates.rutracker_date("15-Мар-26 00:37") == "15.03.2026 00:37:00"


# --- Kinozal: реальный дамп прода --------------------------------------------------

def test_kinozal_parse_garbage_raises():
    with pytest.raises(parsers.SourceParseError, match="kinozal_no_date"):
        parsers.kinozal_parse("<html><title>x</title></html>",
                              "https://kinozal.me/details.php?id=1")


# --- RuTracker: гостевая страница (негативный сценарий — находка 11) ---------------

def test_rutracker_guest_page_fails_loudly():
    html = open(os.path.join(FIX, "rutracker_page.html"),
                encoding="utf-8").read()
    # гостям не видна дата регистрации — парсер обязан сказать об этом
    # явно, а не выдать первую попавшуюся «похожую на дату» строку
    with pytest.raises(parsers.SourceParseError, match="rutracker_no_date"):
        parsers.rutracker_parse(html, "https://rutracker.org/forum/viewtopic.php?t=1")


# --- Anilibria: the answer of its API -------------------------------------------------

# --- браузерная доставка astar/anilibria_tv (Р-9, этап 6) ------------------------

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
