"""Генерация реестра HTTP-эндпоинтов из живого приложения.

Запуск:  .venv/bin/python scripts/gen_endpoints.py [--check]
Выход:   contracts/endpoints.json и разделы 1, 3, 5, 6 файла contracts/endpoints.md (остальное в нём — ручной текст).
         С --check ничего не пишет: говорит ENDPOINTS_OK или ENDPOINTS_STALE <файлы> (код возврата 1).

Реестр и контракты снимаются с объекта FastAPI и его документа OpenAPI, поэтому не расходятся с кодом; то, чего фреймворк не видит
(тело, которое обработчик читает сам, ответ словарём любой формы), задано в MANUAL (scripts/endpoints_md.py), и скрипт не запускается,
пока у такого маршрута нет записи.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ST_DOCS_ENABLED", "true")

from core.bus import Bus  # noqa: E402
from modules.gateway import GatewayModule  # noqa: E402
from scripts.endpoints_md import GROUPS  # noqa: E402

# Краткое назначение эндпоинтов: ключ — "МЕТОД путь", значение — одна строка.
SUMMARIES = {
    "GET /": "Боевой фронт панели (web/dist/index.html)",
    "GET /legacy": "Старый фронт (откат), ассеты по /static",
    "GET /v2": "Редирект 307 на корень",
    "GET /v2/": "Редирект 307 на корень",
    "GET /favicon.svg": "Фавикон",
    "GET /api/stream": "SSE-поток событий шины",
    "POST /api/_diag/echo": "Диагностический прокси к шине (только при diag=True)",
    "POST /api/login": "Вход администратора",
    "GET /api/auth/status": "Статус онбординга: настроен ли админ, вошёл ли клиент",
    "POST /api/setup": "Первичное создание администратора",
    "POST /api/auth/password": "Смена пароля администратором",
    "POST /api/logout": "Выход (очистка сессии)",
    "GET /api/me": "Кто сейчас вошёл",
    "GET /api/series": "Список сериалов",
    "POST /api/series": "Создание серии",
    "GET /api/series/active_torrents": "Прогресс активных загрузок",
    "GET /api/series/{series_id}": "Карточка серии",
    "GET /api/series/{series_id}/sources": "Источники сериала по порядку",
    "POST /api/series/{series_id}/sources": "Добавление источника сериалу (ответ {id})",
    "PUT /api/series/{series_id}/sources/{source_id}": "Изменение полей источника: адрес, профиль, режим поиска, сезон Vibix, отключённые плееры, место",
    "DELETE /api/series/{series_id}/sources/{source_id}": "Удаление источника (единственный не удаляется: last_source)",
    "POST /api/sources/preview": "Что показать окну об адресе, который ещё не источник: состояние таблицы, сводка, строки для конструктора",
    "POST /api/sources/resolve": "Какой сайт читает адрес и его канонический вид (без сети)",
    "GET /api/series/{series_id}/output": "Референс каждой серии и отметка (planner)",
    "PUT /api/series/{series_id}/output/settings": "Значение рычага выхода на уровне сериала, сезона или серии",
    "POST /api/series/{series_id}/output/settings/reset": "Возврат рычагов уровня в наследование",
    "GET /api/voices/settings": "Группы озвучек и порядок источников: как действуют, порядок по умолчанию",
    "GET /api/conform/settings": "Адрес демона выравнивания, рабочая папка здесь и на демоне, очистка очереди по завершении (только чтение): как действуют сейчас",
    "PUT /api/conform/settings": "Записать адрес и рабочую папку целиком (адрес применяется сразу) или получить коды причин отказа",
    "GET /api/provider": "Поставщик внешних источников (Chines): адрес, состояние розетки, манифест, приложения с числом источников и линий, папка загрузок (токен — только признак наличия)",
    "PUT /api/provider": "Записать адрес и токен поставщика (адрес применяется сразу, токен не задан — прежний остаётся, пустой — удаляется) или получить коды причин отказа",
    "GET /api/settings/conform_clear_finished": "Флаг очистки очереди выравнивания по завершении",
    "POST /api/settings/conform_clear_finished": "Записать флаг очистки очереди выравнивания по завершении",
    "DELETE /api/conform/jobs/{job_id}": "Остановить задачу выравнивания, которая ждёт или идёт, либо удалить запись завершённой",
    "GET /api/conform/jobs/{job_id}/plot/{name}": "Картинка графика выравнивания озвучки (PNG), как её сделал демон",
    "PUT /api/voices/settings": "Записать группы и порядок источников целиком или получить коды причин отказа",
    "POST /api/series/{series_id}": "Сохранение свойств серии",
    "DELETE /api/series/{series_id}": "Удаление серии",
    "POST /api/series/{series_id}/state": "Открытие/закрытие статус-окна (viewing)",
    "POST /api/series/{series_id}/toggle_auto_scan": "Включение/выключение автоскана",
    "POST /api/series/{series_id}/ignored-seasons": "Список игнорируемых сезонов",
    "PUT /api/series/{series_id}/ignored-episodes": "Память отключённых серий",
    "POST /api/series/{series_id}/scan": "Синхронный скан одной серии",
    "GET /api/scanner/status": "Состояние планировщика сканирования",
    "POST /api/scanner/settings": "Настройки планировщика (вкл/выкл, интервал)",
    "POST /api/scanner/scan_all": "Полный проход по всем сериалам",
    "GET /api/agent/queue": "Очередь торрент-конвейера",
    "GET /api/downloads/queue": "Очередь загрузок видео",
    "POST /api/downloads/queue/clear": "Очистка очереди загрузок",
    "POST /api/downloads/{task_id}/cancel": "Отмена загрузки: процесс, файл, задача",
    "GET /api/series/{series_id}/composition": "Раскладка видео-сериала (composition)",
    "POST /api/series/{series_id}/reprocess": "Переобработка имён файлов: синхронизация сериала без загрузок (scan)",
    "GET /api/series/{series_id}/source-filenames": "Имена файлов раздач сериала для теста правил торрента",
    "GET /api/auth": "Креды внешних сервисов без секретов",
    "POST /api/auth": "Сохранение кредов сервисов",
    "GET /api/auth/{service}/secret": "Секрет одного сервиса по просьбе глаза его поля (не кэшируется); пустой, если не задан",
    "GET /api/settings/saved_paths": "Список сохранённых путей загрузки",
    "POST /api/settings/saved_paths": "Добавление сохранённого пути",
    "DELETE /api/settings/saved_paths": "Удаление сохранённого пути",
    "GET /api/settings/debug_flags": "Структура логирования с флагами отладки",
    "POST /api/settings/debug_flags": "Включение/выключение отладочного логирования",
    "POST /api/tmdb/search": "Поиск сериала в TMDB",
    "GET /api/tmdb/details/{tmdb_id}": "Детали сериала TMDB",
    "GET /api/trackers": "Список трекеров с зеркалами",
    "PUT /api/trackers/{tracker_id}": "Обновление зеркал трекера",
    "GET /api/directories": "Листинг каталогов",
    "GET /api/parser-profiles": "Список профилей парсера",
    "POST /api/parser-profiles": "Создание профиля парсера",
    "PUT /api/parser-profiles/{profile_id}": "Переименование профиля парсера",
    "DELETE /api/parser-profiles/{profile_id}": "Удаление профиля парсера с правилами",
    "GET /api/parser-profiles/{profile_id}/rules": "Правила профиля",
    "POST /api/parser-profiles/{profile_id}/rules": "Добавление правила в профиль",
    "POST /api/parser-profiles/test": "Тест профиля правил на странице: строки для конструктора и ответ на каждую (composition.preview)",
    "PUT /api/parser-rules/{rule_id}": "Изменение правила",
    "DELETE /api/parser-rules/{rule_id}": "Удаление правила",
    "POST /api/parser-rules/reorder": "Перестановка правил",
    "GET /api/logs": "Чтение логов из logs/*.log",
    "GET /api/logs/groups": "Список групп логов",
    "GET /api/database/tables": "Список таблиц БД",
    "GET /api/database/table/{table_name}": "Все строки таблицы БД",
    "POST /api/database/clear_table": "Очистка таблицы БД",
    "PUT /api/conform/params": "Как просить демон выравнивания: параметры целиком или коды причин отказа",
    "DELETE /api/downloads/cards/{card_id}": "Убрать карточку загрузки, оставшуюся после синхронизации",
    "DELETE /api/pack/cards/{card_id}": "Убрать карточку упаковки, оставшуюся после синхронизации",
    "GET /api/pack/queue": "Очередь упаковки: карточка на контейнер, который собирает синхронизация, его стадии и дорожки",
    "GET /api/rules/line-fields": "Коды полей строки конструктора в его порядке: из них собираются быстрые шаблоны правила «Таблица»",
    "POST /api/schedule": "Времена суток синхронизаций и проверок торрент-раздач; расписание, которое scan отвергает, — отказ 400 с причиной",
    "POST /api/series/{series_id}/failures/reset": "Забыть неудачные попытки сериала: все или одного уровня (сезон, серия, озвучка, нарезки)",
    "GET /api/series/{series_id}/output/queues": "Очереди референса и озвучек групп уровня в порядке ядра, со значениями рычагов",
    "PUT /api/series/{series_id}/output/settings/batch": "Несколько рычагов одной записью: галочки очереди, «снять все»",
    "PUT /api/series/{series_id}/output/switch": "Отключить или включить серии сезона списком, а сезон целиком — без списка",
    "PUT /api/series/{series_id}/output/voice-order": "Порядок озвучек сезона, заданный пользователем: порядок дорожек в контейнерах",
    "PUT /api/series/{series_id}/output/voice-title": "Название дорожки группы озвучек в контейнерах сериала",
    "GET /api/series/{series_id}/plan": "План видео-сериала: актив с происхождением каждого файла, цель каждой серии, неудачи и ждущие команды «Удалить»",
    "POST /api/series/{series_id}/plan/delete": "«Удалить» файла или дорожки в нём: исполнит следующая синхронизация",
    "POST /api/series/{series_id}/plan/delete/cancel": "Вернуть ждущую команду «Удалить»",
    "POST /api/series/{series_id}/scan/cancel": "Остановить синхронизацию сериала: чтение страниц, загрузки, задачи выравнивания, сборку файлов",
    "GET /api/settings/download_limits": "Сколько загрузок идёт одновременно, по способу получения файла",
    "POST /api/settings/download_limits": "Записать пределы загрузок; значение, которое модуль отвергает, — отказ 400 с причиной",
    "GET /api/settings/source_terms": "Сроки сайтов, откуда источники читают страницы",
    "POST /api/settings/source_terms": "Записать срок одного сайта; значение, которое отвергает sources, — отказ 400 с причиной",
    "GET /api/settings/torrent_staging": "Папка, в которую скачиваются раздачи торрент-источников",
    "POST /api/settings/torrent_staging": "Записать папку раздач; значение, которое отвергает torrents, — отказ 400 с причиной",
    "POST /api/sources/cancel": "Остановить идущее чтение страницы",
    "POST /api/sources/fresh": "Когда прочитаны таблицы страниц и до какого срока они свежи",
    "POST /api/sources/read": "Прочитать страницу сейчас, не дожидаясь срока её таблицы",
    "POST /api/sources/search": "Поиск названий у поставщика внешних источников: страница названий как он её даёт",
    "GET /api/sources/settings": "Настройки источников: зеркала каждого трекера и домены, страницы которых читает сервис",
    "POST /api/torrent/check": "Прочитать страницу сейчас: отслеживаемая раздача в другой версии начинает обновление",
    "POST /api/torrent/download": "«Скачать»: раздача уходит в клиент в свою папку",
    "POST /api/torrent/editor": "Редактор торрента страницы: элементы пяти вкладок, папки, неразмещённое, профили",
    "POST /api/torrent/editor/assign": "Разместить вручную: файлы — по сезону и серии, дорожки — по папке",
    "POST /api/torrent/editor/auto": "«Авто»: автоматическая раскладка снова делает удалённые папки и размещает снятые дорожки",
    "POST /api/torrent/editor/folder": "Создать или изменить папку дорожек",
    "POST /api/torrent/editor/folder/delete": "Удалить папку дорожек",
    "POST /api/torrent/editor/remove": "«−»: снять дорожки с их папок",
    "POST /api/torrent/editor/unassign": "Снять ручное размещение",
    "POST /api/torrent/page": "Что перечисляет страница трекера сейчас (чтение с трекера)",
    "POST /api/torrent/releases": "Раздачи, которые держит torrents для страницы: состояние, версия, папка, файлы",
    "POST /api/torrent/watch": "Включить или выключить автоматическое обновление раздачи",
    "GET /api/voices/players": "Каждый плеер порядка источников и его место среди источников сериалов",
}

# Uses of a route by the panel that the static parse of api.METHOD("/literal") cannot see: {(method, path): [sites]}. The stream is opened
# by an EventSource with a query, the plot of an alignment is the address of an image, not a call of the client.
DYNAMIC_PANEL_CALLS: dict[tuple[str, str], list[str]] = {
    ("GET", "/api/stream"): ["composables/useSSE.ts:58"],
    ("GET", "/api/conform/jobs/{job_id}/plot/{name}"): ["lib/work.ts:254"],
}

PUBLIC_EXACT = {"/", "/legacy", "/v2", "/v2/", "/favicon.svg",
                "/api/login", "/api/setup", "/api/auth/status"}
PUBLIC_PREFIX = ("/assets/", "/static/")


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)


def _panel_sites() -> dict[tuple[str, str], list[str]]:
    """Места вызовов API из панели: web/src, статический разбор api.METHOD()."""
    call = re.compile(r"api\.(GET|POST|PUT|DELETE|PATCH)\(\s*([^,)]+)")
    literal = re.compile(r"""^["'](/[^"']+)["']$""")
    out: dict[tuple[str, str], list[str]] = {}
    for path in sorted((ROOT / "web" / "src").rglob("*")):
        if path.suffix not in (".ts", ".vue") or ".test." in path.name:
            continue
        if path.name == "schema.d.ts":
            continue
        rel = str(path.relative_to(ROOT / "web" / "src"))
        lines = path.read_text(encoding="utf-8").splitlines()
        for num, line in enumerate(lines, 1):
            for m in call.finditer(line):
                meth, arg = m.group(1), m.group(2).strip()
                lit = literal.match(arg)
                if lit:
                    out.setdefault((meth, lit.group(1)), []).append(f"{rel}:{num}")
        for num, line in enumerate(lines, 1):
            m = re.search(r"""new EventSource\(["'](/[^"']+)["']\)""", line)
            if m:
                out.setdefault(("GET", m.group(1)), []).append(f"{rel}:{num}")
    return out


def build() -> list[dict]:
    gw = GatewayModule(Bus(), db_path="app.db", secret_key="x",
                        cookie_secure=False, auth_required=False,
                        docs_enabled=True, allowed_hosts=None)
    # Диагностический маршрут существует только при diag=True — снимаем
    # вторым проходом, чтобы реестр был полным.
    gw_diag = GatewayModule(Bus(), db_path="app.db", secret_key="x",
                             cookie_secure=False, auth_required=False,
                             docs_enabled=True, allowed_hosts=None, diag=True)
    panel = _panel_sites()

    entries: list[dict] = []
    for build_tag, app in (("", gw.app), ("diag", gw_diag.app)):
        for route in app.routes:
            methods = sorted(m for m in (getattr(route, "methods", None) or [])
                             if m not in ("HEAD", "OPTIONS"))
            if not methods:
                continue
            if route.path in ("/docs", "/docs/oauth2-redirect", "/openapi.json"):
                continue
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            module = getattr(endpoint, "__module__", "")
            file = "modules/gateway/" + module.rsplit(".", 1)[-1] + ".py" \
                if module.startswith("modules.gateway") else module
            try:
                line = inspect.getsourcelines(endpoint)[1]
            except (OSError, TypeError):
                line = None
            for meth in methods:
                key = (meth, route.path)
                if any(e["method"] == meth and e["path"] == route.path
                       for e in entries):
                    continue
                sites = panel.get(key, [])
                if not sites:
                    sites = list(DYNAMIC_PANEL_CALLS.get(key, []))
                entries.append({
                    "method": meth,
                    "path": route.path,
                    "func": getattr(endpoint, "__name__", ""),
                    "file": file,
                    "line": line,
                    "access": "public" if _is_public(route.path) else "session",
                    "conditional": build_tag or None,
                    "summary": SUMMARIES.get(f"{meth} {route.path}", ""),
                    "panel_sites": sorted(sites),
                })

    entries.sort(key=lambda e: (e["path"], e["method"]))
    return entries


CONSTRUCTOR_TITLE = "Конструктор правил: устройство и словарь"


def _document(entries: list[dict]) -> tuple[str, bool]:
    """The text of contracts/endpoints.md with its generated sections (1, 3, 5, 6) made from the live application, and whether a route is
    left without the text of its body or answer."""
    from scripts.endpoints_md import manual_missing, panel_table_gaps, render, replace_section, vocabulary_gaps

    app = GatewayModule(Bus(), db_path="app.db", secret_key="x", cookie_secure=False, auth_required=False,
                        docs_enabled=True, allowed_hosts=None, diag=True).app
    openapi = app.openapi()
    gaps = manual_missing(entries, openapi, app)
    if gaps:
        print("Маршруты, чьё тело или ответ в MANUAL (scripts/endpoints_md.py) не описаны:\n  " + "\n  ".join(gaps))
        return "", False
    md_path = ROOT / "contracts" / "endpoints.md"
    md = md_path.read_text(encoding="utf-8")
    sections = render(entries, openapi, ROOT)
    # The constructor of the rules is the one hand-written subsection of the contracts: it describes the data of a rule, which no model holds.
    found = re.search(r"### 6\.\d+ " + re.escape(CONSTRUCTOR_TITLE) + r".*?(?=\n### |\n---\n|\n## )", md, re.S)
    hand = re.sub(r"^### 6\.\d+ ", f"### 6.{len(GROUPS) + 1} ", found.group(0), count=1) if found else ""
    stale = vocabulary_gaps(hand)
    if not found or stale:
        print("Подраздел конструктора правил расходится с движком:\n  " + "\n  ".join(stale or ["подраздел не найден"]))
        return "", False
    contracts = sections["contracts"].rstrip("\n") + "\n\n" + hand.rstrip("\n") + "\n\n---\n\n"
    md = replace_section(md, "## 1. Объём", sections["volume"], "## 2. Доступ")
    md = replace_section(md, "## 3. Карта эндпоинтов", sections["map"], "## 4. Функции панели")
    md = replace_section(md, "## 5. Эндпоинты", sections["unused"], "## 6. Контракты")
    md = replace_section(md, "## 6. Контракты", contracts, "## 7. Поток событий SSE")
    stale = panel_table_gaps(md, entries)
    if stale:
        print("Раздел 4 расходится с реестром:\n  " + "\n  ".join(stale))
        return "", False
    return md, True


def main() -> None:
    entries = build()
    text = json.dumps(entries, ensure_ascii=False, indent=1) + "\n"
    target = ROOT / "contracts" / "endpoints.json"
    md_target = ROOT / "contracts" / "endpoints.md"
    document, ok = _document(entries)
    if not ok:
        sys.exit(1)
    if "--check" in sys.argv:
        stale = [path.name for path, new in ((target, text), (md_target, document)) if path.read_text(encoding="utf-8") != new]
        print("ENDPOINTS_STALE " + ", ".join(stale) if stale else "ENDPOINTS_OK")
        sys.exit(1 if stale else 0)
    target.write_text(text, encoding="utf-8")
    md_target.write_text(document, encoding="utf-8")
    paths = {e["path"] for e in entries}
    print(f"{target.relative_to(ROOT)}: {len(entries)} метод-эндпоинтов, {len(paths)} путей; "
          f"{md_target.relative_to(ROOT)}: разделы 1, 3, 5, 6")


if __name__ == "__main__":
    main()
