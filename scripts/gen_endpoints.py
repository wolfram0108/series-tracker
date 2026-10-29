"""Генерация реестра HTTP-эндпоинтов из живого приложения.

Запуск:  .venv/bin/python scripts/gen_endpoints.py
Выход:   contracts/endpoints.json

Реестр снимается с объекта FastAPI, поэтому не расходится с кодом.
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

from core import Bus  # noqa: E402
from modules.gateway import GatewayModule  # noqa: E402

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
    "GET /api/series/{series_id}/voices": "Озвучки сериала по сериям с состоянием каждой",
    "GET /api/voices/settings": "Группы озвучек и порядок источников: как действуют, порядок по умолчанию",
    "GET /api/conform/settings": "Адрес демона выравнивания и рабочая папка здесь и на демоне: как действуют сейчас",
    "PUT /api/conform/settings": "Записать адрес и рабочую папку целиком (адрес применяется сразу) или получить коды причин отказа",
    "PUT /api/voices/settings": "Записать группы и порядок источников целиком или получить коды причин отказа",
    "POST /api/series/{series_id}": "Сохранение свойств серии",
    "DELETE /api/series/{series_id}": "Удаление серии",
    "POST /api/series/{series_id}/state": "Открытие/закрытие статус-окна (viewing)",
    "POST /api/series/{series_id}/toggle_auto_scan": "Включение/выключение автоскана",
    "POST /api/series/{series_id}/ignored-seasons": "Список игнорируемых сезонов",
    "PUT /api/series/{series_id}/ignored-episodes": "Память отключённых серий",
    "PUT /api/series/{series_id}/vk-quality-priority": "Приоритет качества для видео-источника",
    "GET /api/series/{series_id}/torrents/history": "История раздач серии",
    "POST /api/series/{series_id}/scan": "Синхронный скан одной серии",
    "GET /api/scanner/status": "Состояние планировщика сканирования",
    "POST /api/scanner/settings": "Настройки планировщика (вкл/выкл, интервал)",
    "POST /api/scanner/scan_all": "Полный проход по всем сериалам",
    "GET /api/agent/queue": "Очередь торрент-конвейера",
    "GET /api/downloads/queue": "Очередь загрузок видео",
    "POST /api/downloads/queue/clear": "Очистка очереди загрузок",
    "POST /api/downloads/{task_id}/cancel": "Отмена загрузки: процесс, файл, задача",
    "GET /api/series/{series_id}/media-items": "Медиа-элементы серии",
    "PUT /api/media-items/{unique_id}/ignore": "Игнор одного медиа-элемента",
    "PUT /api/series/{series_id}/media-items/ignore-season": "Массовый игнор сезона",
    "POST /api/media-items/{unique_id}/chapters": "Оглавление исходника",
    "POST /api/media-items/{unique_id}/chapters/filtered": "Оглавление с автофильтром мусора",
    "POST /api/media-items/{unique_id}/chapters/mark-garbage": "Ручная разметка мусорных глав",
    "POST /api/media-items/{unique_id}/slice": "Задача нарезки компиляции",
    "POST /api/media-items/{unique_id}/slice-with-filter": "Нарезка с заданным списком отброшенных глав",
    "POST /api/media-items/{unique_id}/delete-source": "Удаление исходника нарезанной компиляции",
    "POST /api/media-items/{unique_id}/verify-sliced-files": "Сверка нарезок с диском",
    "POST /api/series/{series_id}/deep-adoption": "Глубокое усыновление (фон)",
    "GET /api/series/{series_id}/sliced-files": "Нарезки с именем родителя и сезоном",
    "GET /api/series/{series_id}/composition": "Состав композиции",
    "GET /api/series/{series_id}/rename_preview": "Предпросмотр новых имён файлов",
    "POST /api/series/{series_id}/reprocess": "Переобработка имён файлов (торрент)",
    "POST /api/series/{series_id}/reprocess_vk_files": "Переобработка имён файлов (видео)",
    "GET /api/series/{series_id}/source-filenames": "Имена исходных файлов серии",
    "POST /api/preview/naming/fetch": "Фоновый префетч списков файлов раздач",
    "GET /api/auth": "Креды внешних сервисов без секретов",
    "POST /api/auth": "Сохранение кредов сервисов",
    "POST /api/parse_url": "Разбор URL серии: заголовок, раздачи, трекер",
    "GET /api/settings/force_replace": "Чтение флага «всегда заменять торренты»",
    "POST /api/settings/force_replace": "Запись флага «всегда заменять торренты»",
    "GET /api/settings/less_strict_scan": "Чтение флага «менее строгий скан»",
    "POST /api/settings/less_strict_scan": "Запись флага «менее строгий скан»",
    "GET /api/settings/slicing_delete_source": "Чтение флага «удалять исходник после нарезки»",
    "POST /api/settings/slicing_delete_source": "Запись флага «удалять исходник после нарезки»",
    "GET /api/settings/parallel_downloads": "Чтение числа параллельных загрузок",
    "POST /api/settings/parallel_downloads": "Запись числа параллельных загрузок",
    "GET /api/settings/concurrent_fragments": "Чтение числа параллельных фрагментов yt-dlp",
    "POST /api/settings/concurrent_fragments": "Запись числа параллельных фрагментов yt-dlp",
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
    "POST /api/parser-profiles/scrape-titles": "Сбор названий с VK-канала или видеохостинга",
    "POST /api/parser-profiles/test": "Тест профиля правил",
    "PUT /api/parser-rules/{rule_id}": "Изменение правила",
    "DELETE /api/parser-rules/{rule_id}": "Удаление правила",
    "POST /api/parser-rules/reorder": "Перестановка правил",
    "GET /api/logs": "Чтение логов из logs/*.log",
    "GET /api/logs/groups": "Список групп логов",
    "GET /api/database/tables": "Список таблиц БД",
    "GET /api/database/table/{table_name}": "Все строки таблицы БД",
    "POST /api/database/clear_table": "Очистка таблицы БД",
}

# Панель вызывает эти пути через общие хелперы loadFlag/saveFlag/loadNum/saveNum,
# поэтому прямой литерал в api.METHOD() не виден. Список — вызовы хелпера.
DYNAMIC_PANEL_CALLS = {
    ("GET", "/api/settings/force_replace"):
        ["components/settings/SettingsDebug.vue:53",
         "components/settings/SettingsDebug.vue:163",
         "components/settings/SettingsDebug.vue:200"],
    ("POST", "/api/settings/force_replace"):
        ["components/settings/SettingsDebug.vue:57",
         "components/settings/SettingsDebug.vue:200"],
    ("GET", "/api/settings/less_strict_scan"):
        ["components/settings/SettingsDebug.vue:53",
         "components/settings/SettingsDebug.vue:164",
         "components/settings/SettingsDebug.vue:204"],
    ("POST", "/api/settings/less_strict_scan"):
        ["components/settings/SettingsDebug.vue:57",
         "components/settings/SettingsDebug.vue:204"],
    ("GET", "/api/settings/slicing_delete_source"):
        ["components/settings/SettingsDebug.vue:53",
         "components/settings/SettingsDebug.vue:165",
         "components/settings/SettingsDebug.vue:208"],
    ("POST", "/api/settings/slicing_delete_source"):
        ["components/settings/SettingsDebug.vue:57",
         "components/settings/SettingsDebug.vue:208"],
    ("GET", "/api/settings/parallel_downloads"):
        ["components/settings/SettingsDebug.vue:68",
         "components/settings/SettingsDebug.vue:166",
         "components/settings/SettingsDebug.vue:219"],
    ("POST", "/api/settings/parallel_downloads"):
        ["components/settings/SettingsDebug.vue:73",
         "components/settings/SettingsDebug.vue:219"],
    ("GET", "/api/settings/concurrent_fragments"):
        ["components/settings/SettingsDebug.vue:68",
         "components/settings/SettingsDebug.vue:167",
         "components/settings/SettingsDebug.vue:228"],
    ("POST", "/api/settings/concurrent_fragments"):
        ["components/settings/SettingsDebug.vue:73",
         "components/settings/SettingsDebug.vue:228"],
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


def main() -> None:
    entries = build()
    target = ROOT / "contracts" / "endpoints.json"
    target.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    paths = {e["path"] for e in entries}
    methods = len(entries)
    print(f"{target.relative_to(ROOT)}: {methods} метод-эндпоинтов, "
          f"{len(paths)} путей")


if __name__ == "__main__":
    main()
