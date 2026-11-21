"""The sections of contracts/endpoints.md that are made from the live application, not written by hand: the volume (1), the map of the
routes (3), the routes the panel does not call (5) and the contracts of the routes (6).

The registry `contracts/endpoints.json` (what each route is, where it is, who calls it) and the OpenAPI document of the gateway (what each
route takes and answers) are the two sources; a text that is in neither is not in these sections. A body or an answer the framework cannot
see (the handler reads `await request.json()` itself, or answers a dictionary of any shape) is told by `MANUAL` below, and the generator
refuses to run when a route of that kind has no entry, so that nothing is left out silently. The two hand-written parts that name things
of the code are checked against it: the constructor's subsection against the vocabulary of the engine, the table of the panel's functions
against the routes the panel calls.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

METHODS = ("GET", "POST", "PUT", "DELETE")
ORDER = {method: index for index, method in enumerate(METHODS)}

# The groups of the document: the file of the handler, the number of the subsection and its title.
GROUPS = (
    ("api_auth.py", "Авторизация"),
    ("api_series.py", "Серии"),
    ("api_media.py", "Медиа и файлы"),
    ("api_system.py", "Скан и очереди"),
    ("api_settings.py", "Настройки, справочники, логи, БД"),
    ("module.py", "Служебные"),
    ("api_sources.py", "Источники"),
    ("api_output.py", "Выход"),
    ("api_torrents.py", "Торрент-источник"),
)

# What the framework does not see, by route: `body` is what the handler reads from the body of the request itself, `answer` what it answers when
# the schema is a dictionary of any shape. Written from the handlers (modules/gateway/api_*.py).
MANUAL: dict[tuple[str, str], dict[str, str]] = {
    ("POST", "/api/login"): {"body": "`{username, password}`", "answer": "`{success, username}`; 401 — неверная пара, 429 — адрес забанен, 503 — модуль `auth` не отвечает"},
    ("GET", "/api/stream"): {"answer": "поток `text/event-stream`: кадр на событие шины (`frames=each`, по умолчанию) или на ход шины (`frames=batch`); раздел 7"},
    ("GET", "/api/auth/status"): {"answer": "`{configured, authenticated, username, admin}`"},
    ("POST", "/api/setup"): {"body": "`{username, password}` (пароль не короче 8 знаков)", "answer": "`{success, username}`; 400 — имя пусто или пароль короток, 403 — администратор уже есть"},
    ("POST", "/api/auth/password"): {"body": "`{new}` (не короче 8 знаков)", "answer": "`{success}`; 400 — пароль короток, 401 — нет сессии"},
    ("POST", "/api/logout"): {"answer": "`{success}`"},
    ("GET", "/api/me"): {"answer": "`{authenticated, username}`; 401 без сессии"},
    ("POST", "/api/series/{series_id}/state"): {"body": "`{state: [\"viewing\"]}` при открытии окна, `{state: []}` при закрытии"},
    ("POST", "/api/series/{series_id}/toggle_auto_scan"): {"body": "`{enabled}` (обязательно, иначе 400)"},
    ("POST", "/api/series/{series_id}/ignored-seasons"): {"body": "`{seasons: [...]}` (обязательно, иначе 400); в карточке хранится строкой JSON"},
    ("PUT", "/api/series/{series_id}/ignored-episodes"): {"body": "`{episodes: {\"<сезон>\": [номера]}}` (обязательно, иначе 400); в карточке хранится строкой JSON"},
    ("POST", "/api/scanner/settings"): {"body": "`{enabled?}` — выключатель планировщика"},
    ("POST", "/api/schedule"): {"answer": "`{schedule}` — расписание как оно действует; 400 `{error}` — расписание, которое отвергает `scan`"},
    ("GET", "/api/auth"): {"answer": "по каждому сервису (`qbittorrent`, `kinozal`, `vk`, `rutracker`) `{username, url, has_password}` (секретов нет; `{}`, если записи нет), `tmdb` — `{configured}`"},
    ("POST", "/api/auth"): {"body": "`{qbittorrent?, kinozal?, rutracker?: {username, password?, url?}, vk?: {token?}, tmdb?: {token?}}`; пустой пароль или токен — «не менять»"},
    ("GET", "/api/auth/{service}/secret"): {"answer": "`{secret}` (пусто, если не задан), `Cache-Control: no-store`; 404 — службы нет"},
    ("GET", "/api/settings/conform_clear_finished"): {"answer": "`{enabled}`"},
    ("POST", "/api/settings/conform_clear_finished"): {"body": "`{enabled}`", "answer": "`{success}`"},
    ("GET", "/api/settings/download_limits"): {"answer": "`{threads_total, threads_per_file, single_at_once, provider_at_once, multi_at_once}`"},
    ("POST", "/api/settings/download_limits"): {"body": "любое из `threads_total`, `threads_per_file`, `single_at_once`, `provider_at_once` (целое от 1; не присланное не меняется)", "answer": "пределы как они стали (как в GET); 400 `{error}` — значение отвергнуто"},
    ("GET", "/api/settings/source_terms"): {"answer": "`{sites: [{site, provider, sources, minutes}]}`"},
    ("POST", "/api/settings/source_terms"): {"body": "`{site, minutes}` (минуты — целое от одной до месяца)", "answer": "`{success, minutes}`; 400 `{error}` — значение отвергнуто"},
    ("GET", "/api/settings/torrent_staging"): {"answer": "`{value}`"},
    ("POST", "/api/settings/torrent_staging"): {"body": "`{value}` (абсолютный путь)", "answer": "`{success, value}`; 400 `{error}` — значение отвергнуто"},
    ("GET", "/api/settings/saved_paths"): {"answer": "`{paths: [{id, path}]}`"},
    ("POST", "/api/settings/saved_paths"): {"body": "`{path}` (пустая строка не добавляется)", "answer": "`{paths}` — актуальный список"},
    ("DELETE", "/api/settings/saved_paths"): {"body": "`{id}` (в теле, не в адресе)", "answer": "`{paths}` — актуальный список"},
    ("GET", "/api/settings/debug_flags"): {"answer": "`{logging_modules: {<раздел>: [{name, enabled}]}}`"},
    ("POST", "/api/settings/debug_flags"): {"body": "`{module, enabled}` (`module` обязателен, иначе 400)", "answer": "`{success}`"},
    ("POST", "/api/tmdb/search"): {"body": "`{query}` (пустой — 400)", "answer": "`{success, results}`; 400 — отказ модуля"},
    ("GET", "/api/tmdb/details/{tmdb_id}"): {"answer": "`{success, ...}` — подробности как их дала модель `metadata` (`TmdbDetails`); 404 `{success, error}` — отказ модуля"},
    ("GET", "/api/trackers"): {"answer": "список трекеров с зеркалами (`Tracker`)"},
    ("PUT", "/api/trackers/{tracker_id}"): {"body": "`{mirrors}` (обязательно, иначе 400)"},
    ("GET", "/api/directories"): {"answer": "список каталогов пути (`DirectoryListing`); 403 `{error}` — путь закрыт или не читается, 400 `{error}` — иной отказ"},
    ("POST", "/api/parser-profiles"): {"body": "`{name}` (пусто — 400)", "answer": "201 `{success, id}`; 409 — отказ модуля"},
    ("PUT", "/api/parser-profiles/{profile_id}"): {"body": "`{name}` (пусто — 400)", "answer": "`{success}`; 409 — отказ модуля"},
    ("GET", "/api/parser-profiles"): {"answer": "список профилей с полями, которые дал `rules`"},
    ("GET", "/api/parser-profiles/{profile_id}/rules"): {"answer": "список правил профиля с полями, которые дал `rules`"},
    ("POST", "/api/parser-profiles/{profile_id}/rules"): {"body": "данные правила (раздел 6.10)", "answer": "`{success, id}`; 400 — отказ модуля"},
    ("PUT", "/api/parser-rules/{rule_id}"): {"body": "данные правила (раздел 6.10)"},
    ("POST", "/api/parser-rules/reorder"): {"body": "массив номеров правил в новом порядке"},
    ("GET", "/api/logs"): {"answer": "список записей журнала (`group`, `level`, `message`, `timestamp`, ...), новые первыми"},
    ("GET", "/api/database/table/{table_name}"): {"answer": "список строк таблицы; 403 — таблица закрыта"},
    ("POST", "/api/database/clear_table"): {"body": "`{table_name}` (пусто — 400)", "answer": "`{success, message}`; 500 — таблицы нет или она закрыта"},
    ("POST", "/api/_diag/echo"): {"body": "`{topic, payload?, timeout?}`", "answer": "`{reply}` — ответ шины; 502 — отказ"},
    ("POST", "/api/sources/fresh"): {"answer": "`{pages: [{url, read_at, fresh_until}]}` — когда прочитана таблица каждой страницы и до какого срока она свежа; страница без таблицы не названа"},
    ("GET", "/api/series/active_torrents"): {"answer": "список задач монитора (`tasks`)"},
    ("GET", "/api/agent/queue"): {"answer": "список задач очереди торрент-конвейера"},
    ("POST", "/api/torrent/page"): {"answer": "что перечисляет страница трекера (`PageReleases`)"},
    ("POST", "/api/torrent/releases"): {"answer": "раздачи, которые держит `torrents` для страницы (`ReleasesReply`)"},
    ("POST", "/api/torrent/download"): {"answer": "раздача после начала загрузки (`ReleaseView`)"},
    ("POST", "/api/torrent/check"): {"answer": "раздачи, у которых началось обновление или оно ждёт синхронизации (`ReleasesChecked`)"},
    ("POST", "/api/torrent/watch"): {"answer": "раздача после смены слежения (`ReleaseView`)"},
    ("POST", "/api/torrent/editor"): {"answer": "`{url, profiles, view}` — редактор страницы: профили пяти вкладок и раскладка (`view`: раздачи с ходом чтения их файлов, элементы вкладок, папки, неразмещённое); при первом открытии страницы профили создаются"},
}

_UNIVERSAL = {"HTTPValidationError"}


def _ref(schema: dict) -> str | None:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "allOf" in schema and len(schema["allOf"]) == 1 and "$ref" in schema["allOf"][0]:
        return schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    return None


def type_of(schema: dict) -> str:
    """A schema as a short type: a model by its name, a list by its items, a union by its parts."""
    if (name := _ref(schema)) is not None:
        return name
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(type_of(each) for each in schema[key])
    if "enum" in schema:
        return " | ".join(json.dumps(each, ensure_ascii=False) for each in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    kind = schema.get("type")
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(type_of(each) for each in schema["prefixItems"]) + "]"
        return type_of(schema.get("items", {})) + "[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"object<{type_of(extra)}>" if isinstance(extra, dict) and extra else "object"
    if kind == "string" and schema.get("format"):
        return f"string ({schema['format']})"
    return kind or "any"


def _default(schema: dict) -> str:
    return f", по умолчанию `{json.dumps(schema['default'], ensure_ascii=False)}`" if "default" in schema else ""


def fields_of(name: str, schemas: dict) -> list[str]:
    """The fields of a model, one line each: the type, whether it is required, its default."""
    model = schemas.get(name, {})
    if "properties" not in model:
        return []
    required = set(model.get("required", []))
    lines = []
    for field, schema in model["properties"].items():
        mark = "обязательное" if field in required else "необязательное"
        lines.append(f"`{field}` — {type_of(schema)}, {mark}{_default(schema)}")
    return lines


def _model_block(name: str, schemas: dict, request: bool, told: bool, indent: str = "  ") -> list[str]:
    """The lines under a body or an answer that is a model: whether an unknown field is refused (a body only), then its fields; a model with no
    fields is of any shape, which `told` (the route has a text for it in `MANUAL`) makes needless to say."""
    model = schemas.get(name, {})
    out = []
    out += [f"{indent}- {line}" for line in fields_of(name, schemas)]
    if not model.get("properties") and not told:
        out.append(f"{indent}форма произвольная (поля зависят от данных)")
    return out


def _body_lines(operation: dict, manual: dict, schemas: dict) -> list[str]:
    body = operation.get("requestBody")
    if body is None:
        return [f"- Тело (читает сам обработчик): {manual['body']}"] if "body" in manual else []
    schema = body["content"]["application/json"]["schema"]
    required = "" if body.get("required") else " (необязательно)"
    name = _ref(schema)
    if name is None:
        for option in schema.get("anyOf", []):
            if _ref(option):
                name = _ref(option)
    if name is None:
        return [f"- Тело{required}: {type_of(schema)}"]
    refuses = " (лишнее поле отклоняется — 422)" if schemas.get(name, {}).get("additionalProperties") is False else ""
    return [f"- Тело{required}: `{name}`{refuses}", *_model_block(name, schemas, True, "body" in manual)]


def _answer_lines(operation: dict, manual: dict, schemas: dict) -> list[str]:
    out = []
    answers = {}
    for status, response in operation.get("responses", {}).items():
        if status == "422":
            continue
        content = response.get("content", {}).get("application/json")
        shape = type_of(content["schema"]) if content else "без тела JSON"
        if "image/png" in response.get("content", {}):
            shape = "картина PNG"
        answers.setdefault(shape, []).append(status)
    for shape, statuses in answers.items():
        name = shape.removesuffix("[]")
        if shape == "any":
            out.append(f"- Ответ `{', '.join(statuses)}`: " + (manual["answer"] if "answer" in manual else "JSON произвольной формы"))
            continue
        line = f"- Ответ `{', '.join(statuses)}`: `{shape}`"
        if shape.endswith("[]"):
            line += " (список)"
        out.append(line)
        if name in {"ErrorResponse", "ErrorOnly"}:
            out[-1] += " — {" + ", ".join(f"`{field}`" for field in schemas[name]["properties"]) + "}"
        elif name in schemas:
            out += _model_block(name, schemas, False, "answer" in manual)
    if "answer" in manual and "any" not in answers:
        out.append(f"- Форма ответа: {manual['answer']}")
    return out


def _panel_cell(sites: list[str]) -> str:
    names = []
    for site in sites:
        name = site.rsplit("/", 1)[-1].split(":")[0]
        if name not in names:
            names.append(name)
    return ", ".join(f"`{each}`" for each in names) if names else "—"


def _group_of(entry: dict) -> str:
    return entry["file"].rsplit("/", 1)[-1]


def render(entries: list[dict], openapi: dict, root: Path) -> dict[str, str]:
    """The text of each generated section: `volume`, `map`, `unused`, `contracts`."""
    schemas = openapi["components"]["schemas"]
    by_key = {(e["method"], e["path"]): e for e in entries}
    api = [e for e in entries if e["path"].startswith("/api") and not e["conditional"]]
    by_file: dict[str, set] = {}
    for e in entries:
        by_file.setdefault(_group_of(e), set()).add(e["path"])
    outside = {e["path"] for e in entries if not e["path"].startswith("/api")}
    files = ", ".join(f"`{name}` ({len(paths)})" for name, paths in sorted(by_file.items()) if name != "module.py")
    volume = f"""## 1. Объём

| | |
|---|---|
| Уникальных путей | {len({e['path'] for e in entries})} |
| Метод-эндпоинтов (путь × метод) | {len(entries)} |
| Путей под `/api` | {len({e['path'] for e in api})} |
| Метод-эндпоинтов под `/api` | {len(api)} |
| Служебных путей вне `/api` | {len(outside)} в реестре: {', '.join(f'`{p}`' for p in sorted(outside))}; `/favicon.svg` отдаётся при собранном `web/dist` |

Один маршрут условный: `POST /api/_diag/echo` существует только при запуске
gateway с `diag=True` (в проде выключен) — в таблице {len(entries)} он учтён, в счёт
{len(api)} метод-эндпоинтов под `/api` — нет.

`/docs` и `/openapi.json` (собственные маршруты FastAPI) появляются только
при `ST_DOCS_ENABLED=true` и в реестр не входят.

Файлы-источники: {files},
`module.py` ({len(by_file['module.py'])}: служебные пути, `/api/stream` и диагностический).

---

"""

    # --- 3: the map -------------------------------------------------------------------------------------------------------
    map_lines = ["## 3. Карта эндпоинтов", "",
                 "Колонка «Панель» — где вызывается из `web/src` (по `panel_sites` реестра `endpoints.json`); «—» означает, что из панели",
                 "эндпоинт не вызывается (раздел 5). Описание — из реестра (`SUMMARIES` в `scripts/gen_endpoints.py`).", ""]
    for number, (file, title) in enumerate(GROUPS, 1):
        rows: dict[str, list[dict]] = {}
        for e in sorted((x for x in entries if _group_of(x) == file), key=lambda x: (x["path"], ORDER[x["method"]])):
            rows.setdefault(e["path"], []).append(e)
        if not rows:
            continue
        map_lines += [f"### 3.{number} {title} — `{file}`", "", "| Метод | Путь | Назначение | Панель |", "|---|---|---|---|"]
        for path, items in rows.items():
            methods = ", ".join(e["method"] for e in items)
            summaries = {e["summary"] for e in items}
            summary = items[0]["summary"] if len(summaries) == 1 else "; ".join(f"{e['method']}: {e['summary']}" for e in items)
            sites = [site for e in items for site in e["panel_sites"]]
            panel = "браузер" if not path.startswith("/api") and path not in ("/api/stream",) else _panel_cell(sites)
            map_lines.append(f"| {methods} | `{path}` | {summary} | {panel} |")
        map_lines.append("")
    map_lines += ["---", "", ""]

    # --- 5: the routes the panel does not call ------------------------------------------------------------------------------------
    def users(base: str, suffixes: set, path: str) -> bool:
        pieces = re.split(r"(\{[^}]+\})", path)
        regex = re.compile("".join(r"(?:[^/\"'`]+|\$\{[^}]+\})" if each.startswith("{") else re.escape(each) for each in pieces if each)
                           + r"(?=[\"'`?\s)])")
        for found in sorted((root / base).rglob("*")):
            if found.suffix in suffixes and "node_modules" not in found.parts and found.name != "schema.d.ts":
                if regex.search(found.read_text(encoding="utf-8", errors="ignore")):
                    return True
        return False

    unused_rows = []
    unused = [e for e in sorted(api, key=lambda x: (x["path"], ORDER[x["method"]])) if not e["panel_sites"]]
    for e in unused:
        legacy = users("legacy_frontend", {".js", ".html"}, e["path"])
        tests = users("tests", {".py"}, e["path"])
        tools = users("tools", {".py"}, e["path"])
        who = "`legacy_frontend`" if legacy else "только тесты" if tests else "только прибор `tools/checks/window_json.py`" if tools else "никто"
        status = ("доступен, но только старому фронту" if legacy else "только тесты; в панели нет вызова" if tests else
                  "только прибор сверки окна; в панели, у старого фронта и в тестах вызова нет" if tools else "нигде не вызывается")
        unused_rows.append(f"| {e['method']} | `{e['path']}` | {who} | {status} |")
    unused_text = f"""## 5. Эндпоинты, не используемые панелью

{len(unused)} из {len(api)} метод-эндпоинтов под `/api` панель не вызывает (в реестре `contracts/endpoints.json` у них пуст `panel_sites`).
«Кто вызывает» — поиск адреса в `legacy_frontend/`, `tests/` и `tools/`.

| Метод | Путь | Кто вызывает | Статус |
|---|---|---|---|
""" + "\n".join(unused_rows) + "\n\n---\n\n"

    # --- 6: the contracts -----------------------------------------------------------------------------------------------------------
    blocks = ["## 6. Контракты", "",
              "Разделы 6.1–6.9 сняты с документа OpenAPI шлюза (модели тел и ответов) и со списка `MANUAL` в `scripts/endpoints_md.py` для того, что",
              "обработчик читает сам (`await request.json()`) или отдаёт словарём любой формы. Тело, у которого модель с `extra=\"forbid\"`, отклоняет",
              "поле, которого модель не знает, кодом 422 с его именем. 422 есть у каждого маршрута с телом или параметрами (`HTTPValidationError`).",
              "Описание маршрута — его докстринг (источник — код). Вложенные модели показаны именем; их поля — в документе OpenAPI (`/openapi.json`,",
              "`ST_DOCS_ENABLED=true`) и в `core/contracts`.", ""]
    missing = []
    for number, (file, title) in enumerate(GROUPS, 1):
        items = sorted((e for e in entries if _group_of(e) == file), key=lambda x: (x["path"], ORDER[x["method"]]))
        if not items:
            continue
        blocks += [f"### 6.{number} {title}", ""]
        for e in items:
            if not e["path"].startswith("/api"):
                continue
            operation = openapi["paths"].get(e["path"], {}).get(e["method"].lower())
            if operation is None:
                continue
            manual = MANUAL.get((e["method"], e["path"]), {})
            blocks.append(f"**{e['method']} `{e['path']}`** — {e['summary']}.")
            description = (operation.get("description") or "").strip()
            if description:
                blocks += ["", *(f"> {line}" if line.strip() else ">" for line in description.split("\n"))]
            blocks.append("")
            params = [p for p in operation.get("parameters", []) if p["in"] == "query"]
            for p in params:
                blocks.append(f"- Query `{p['name']}`: {type_of(p['schema'])}{'' if p.get('required') else ', необязательный'}{_default(p['schema'])}")
            blocks += _body_lines(operation, manual, schemas)
            blocks += _answer_lines(operation, manual, schemas)
            blocks.append("")
    contracts = "\n".join(blocks) + "\n"
    return {"volume": volume, "map": "\n".join(map_lines), "unused": unused_text, "contracts": contracts, "missing": missing}


def manual_missing(entries: list[dict], openapi: dict, app) -> list[str]:
    """The routes whose handler reads its body itself or answers a dictionary of any shape, and that `MANUAL` does not tell."""
    schemas = openapi["components"]["schemas"]
    missing = []
    endpoints = {}
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            endpoints[(method, route.path)] = getattr(route, "endpoint", None)
    for e in entries:
        key = (e["method"], e["path"])
        operation = openapi["paths"].get(e["path"], {}).get(e["method"].lower())
        endpoint = endpoints.get(key)
        if operation is None or endpoint is None or not e["path"].startswith("/api"):
            continue
        manual = MANUAL.get(key, {})
        reads_body = e["method"] != "GET" and "request.json()" in inspect.getsource(endpoint) and "requestBody" not in operation
        shapeless = False
        ok = operation["responses"].get("200", {}).get("content", {}).get("application/json", {}).get("schema")
        if ok is not None:
            target = _ref(ok) or _ref(ok.get("items", {})) or ""
            shapeless = target in {"DynamicObject", "QueueTask"} or type_of(ok) == "any"
        if (reads_body and "body" not in manual) or (shapeless and "answer" not in manual):
            missing.append(f"{e['method']} {e['path']}")
    return missing


def vocabulary_gaps(text: str) -> list[str]:
    """The words of the constructor's subsection that the engine does not know, and the engine's words the subsection does not
    tell: its vocabulary is `calc/rules/engine.py`, and a document that names a value the engine lacks (or leaves one out) is stale."""
    from calc.rules.engine import ACTION_TYPES, BLOCK_TYPES, CONDITION_TYPES

    known = set(ACTION_TYPES) | set(BLOCK_TYPES) | set(CONDITION_TYPES)
    unknown = sorted(set(re.findall(r"`((?:assign|extract)_[a-z_]+)`", text)) - known)
    left_out = sorted(word for word in known if f"`{word}`" not in text)
    return [f"в подразделе конструктора назван тип, которого нет в движке: {word}" for word in unknown] + \
           [f"в подразделе конструктора не назван тип движка: {word}" for word in left_out]


def panel_table_gaps(md: str, entries: list[dict]) -> list[str]:
    """The rows of section 4 (the panel's functions, written by hand) that name a route the gateway does not have, or one the panel
    does not call: the registry knows both."""
    by_key = {(e["method"], e["path"]): e for e in entries}
    start = md.index("## 4. Функции панели")
    gaps = []
    for line in md[start:md.index("## 5. Эндпоинты", start)].split("\n"):
        found = re.match(r"\| (.+) \| ([A-Z, ]+) \| `(/[^`]*)`", line)
        for method in ([part.strip() for part in found.group(2).split(",")] if found else []):
            entry = by_key.get((method, found.group(3)))
            if entry is None:
                gaps.append(f"раздел 4: маршрута нет в шлюзе: {method} {found.group(3)}")
            elif not entry["panel_sites"]:
                gaps.append(f"раздел 4: панель не вызывает: {method} {found.group(3)} ({found.group(1)})")
    return gaps


def replace_section(md: str, heading: str, text: str, next_heading: str) -> str:
    """The text of the section that begins at `heading` and ends before `next_heading` is replaced."""
    start = md.index(heading)
    end = md.index(next_heading, start + len(heading))
    return md[:start] + text + md[end:]
