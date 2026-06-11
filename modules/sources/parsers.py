"""Чистые функции разбора HTML трекеров: html -> {title, releases}.

Отделены от доставки страниц (тестируются на дампах без сети/браузера).
Контракт результата: {"title": {"ru", "en"}, "releases": [
    {"link"|None, "magnet"|None, "date_marker", "episodes", "quality"}]}.
Никаких решений «ново/старо» — только факты (Р-9); дельту считает scan.

Селекторы детерминированные (находка 11): если ожидаемый элемент не
найден — SourceParseError с понятным текстом, а не поиск «чего-нибудь
похожего где-нибудь».
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import dates


class SourceParseError(RuntimeError):
    pass


def _release(link=None, magnet=None, date_marker=None, episodes=None,
             quality=None, **extra) -> dict:
    return {"link": link, "magnet": magnet, "date_marker": date_marker,
            "episodes": episodes, "quality": quality, **extra}


# --- Kinozal: страница = одна раздача -------------------------------------------

def kinozal_parse(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)

    title_tag = soup.find("title")
    title_ru = None
    if title_tag:
        title_ru = re.sub(r"\s*::\s*Кинозал\.(ТВ|МЕ)$", "",
                          title_tag.text.strip(), flags=re.IGNORECASE).strip()

    raw_date = None
    for key_word in ("Обновлен", "Залит"):
        li = soup.find(lambda t: t.name == "li" and t.contents
                       and key_word in str(t.contents[0]))
        if li and (span := li.find("span", class_="floatright")):
            raw_date = span.get_text(strip=True)
            break
    if not raw_date:  # запасной источник: баннер «Торрент-файл обновлен ...»
        banner = soup.find("div", class_="bx1 justify",
                           string=re.compile(r"Торрент-файл обновлен"))
        if banner:
            m = re.search(r"Торрент-файл обновлен\s+(.*?)\s*Чтобы",
                          banner.get_text(strip=True))
            raw_date = m.group(1) if m else None
    if not raw_date:
        raise SourceParseError("Kinozal: дата обновления не найдена")

    link_tag = soup.find("a", href=lambda h: h and "download.php?id=" in h)
    if not link_tag:
        raise SourceParseError("Kinozal: ссылка download.php не найдена")
    m = re.search(r"(download\.php\?id=\d+)", link_tag["href"])
    href = m.group(1) if m else link_tag["href"].lstrip("/")
    link = f"{parsed.scheme}://dl.{parsed.netloc}/{href}"

    return {"title": {"ru": title_ru, "en": None},
            "releases": [_release(link=link,
                                  date_marker=dates.kinozal_date(raw_date))]}


# --- RuTracker: страница = одна раздача ------------------------------------------

def rutracker_parse(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    h1 = soup.find("h1", class_="maintitle")
    title_ru = h1.get_text(strip=True) if h1 else None

    raw_date = None
    attach = soup.find("table", class_="attach")
    if attach:
        m = re.search(
            r"Зарегистрирован[^0-9]*(\d{2}-[А-Яа-яA-Za-zёЁ]{3}-\d{2}\s\d{2}:\d{2})",
            attach.get_text())
        raw_date = m.group(1) if m else None
    if not raw_date:
        raise SourceParseError(
            "RuTracker: дата регистрации не найдена в таблице attach "
            "(гостевая сессия? нужен логин)")

    dl = soup.find("a", class_="dl-stub")
    if not dl or dl.get("href", "").startswith("magnet:"):
        raise SourceParseError("RuTracker: ссылка a.dl-stub не найдена")
    href = dl["href"]
    if not href.startswith("http"):
        if "dl.php" in href and "/forum/" not in href:
            href = ("/forum/" + href.lstrip("/")) if not href.startswith("/") \
                else href.replace("/dl.php", "/forum/dl.php")
        link = base + (href if href.startswith("/") else "/" + href)
    else:
        link = href

    magnet_tag = soup.find("a", class_="magnet-link")
    return {"title": {"ru": title_ru, "en": None},
            "releases": [_release(
                link=link,
                magnet=magnet_tag.get("href") if magnet_tag else None,
                date_marker=dates.rutracker_date(raw_date))]}


# --- Astar: страница = каталог раздач (вторая очередь, доставка — браузер) -------

def astar_parse(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    h1 = soup.find("h1")
    title_ru = h1.text.strip() if h1 else None

    releases = []
    for item in soup.find_all("div", class_="torrent"):
        link_tag = item.find("a", href=re.compile(r"/engine/gettorrent\.php\?id=\d+"))
        if not link_tag:
            continue
        link = base + link_tag["href"]

        date_marker = None
        for div in item.find_all("div", class_="bord_a1"):
            m = re.search(r"Дата: (\d{2}-\d{2}-\d{4})",
                          re.sub(r"\s+", " ", div.text.strip()))
            if m:
                date_marker = dates.astar_date(m.group(1))
                break

        info = item.find("div", class_="info_d1")
        text = info.text.strip() if info else ""
        text = re.sub(r"\s*END\s*", "", text).strip()
        text = re.sub(r"\s*\(\d+\.\d+\s*(Mb|Gb)\)", "", text).strip()
        episodes = quality = None
        for pattern, label in ((r"^Серии\s+(\d+-\d+)(?:\s+(.+))?$", None),
                               (r"^Серия\s+(\d+)(?:\s+(.+))?$", None),
                               (r"^Спешл\s+(\d+)(?:\s+(.+))?$", "Спешл ")):
            if m := re.match(pattern, text):
                episodes = (label or "") + m.group(1)
                quality = m.group(2) or "one"
                break
        if episodes:
            releases.append(_release(link=link, date_marker=date_marker,
                                     episodes=episodes, quality=quality))

    # исторический хак Astar: версия без пометки качества при наличии
    # других версий того же диапазона помечается как 'old'
    by_episodes: dict[str, list] = {}
    for r in releases:
        by_episodes.setdefault(r["episodes"], []).append(r["quality"])
    for episodes, qualities in by_episodes.items():
        if len(qualities) > 1 and "one" in qualities:
            for r in releases:
                if r["episodes"] == episodes and r["quality"] == "one":
                    r["quality"] = "old"

    return {"title": {"ru": title_ru, "en": None}, "releases": releases}


# --- Anilibria.TV: каталог (вторая очередь, доставка — браузер) -------------------

def _anilibria_tv_en_title(full_title: str) -> str:
    parts = [p.strip() for p in (full_title or "").split("/")]
    latin = [p for p in parts if p and not re.search(r"[а-яА-Я]", p)]
    return min(latin, key=len) if latin else ""


def anilibria_tv_parse(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    title_tag = soup.find("title")
    full_title = title_tag.get_text(strip=True) if title_tag else ""

    table = soup.find("table", id="publicTorrentTable")
    if not table:
        raise SourceParseError("Anilibria.TV: таблица publicTorrentTable не найдена")

    releases = []
    for row in table.find_all("tr"):
        if not row.find("td"):
            continue
        link_tag = row.find("a", class_="torrent-download-link")
        if not (link_tag and link_tag.has_attr("href")):
            continue
        date_td = row.find("td", class_="torrent-datetime")
        date_iso = (date_td.get("data-datetime")
                    if date_td and date_td.has_attr("data-datetime") else None)
        info_td = row.find("td", class_="torrentcol1")
        info = info_td.get_text(strip=True) if info_td else ""
        episodes, quality = info, None
        if m := re.match(r"(.+?)\s*\[(.+)\]", info):
            episodes, quality = m.group(1).strip(), m.group(2).strip()
        releases.append(_release(
            link=base + link_tag["href"],
            date_marker=dates.anilibria_tv_date(date_iso) if date_iso else None,
            episodes=episodes, quality=quality))

    return {"title": {"ru": full_title,
                      "en": _anilibria_tv_en_title(full_title)},
            "releases": releases}
