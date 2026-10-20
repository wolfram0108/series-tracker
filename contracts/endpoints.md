# HTTP API — справочник (v2)

> Актуально на **2026-10-20**, коммит `1f016f4`. Источник истины — код
> `modules/gateway/api_*.py`; перечень снят с живого приложения
> (`GatewayModule._create_app`), а не из документации.
>
> Документ заменил реестр v1 от 2026-06-11, который описывал Flask-блюпринты
> `routes/*.py` — файлов этого модуля в системе v2 нет.

---

## 1. Объём

| | |
|---|---|
| Уникальных путей | 74 |
| Метод-эндпоинтов (путь × метод) | 90 |
| Путей под `/api` | 68 |
| Метод-эндпоинтов под `/api` | 84 |
| Служебных путей вне `/api` | 5: `/`, `/legacy`, `/v2`, `/v2/`, `/favicon.svg` |

Один маршрут условный: `POST /api/_diag/echo` существует только при запуске
gateway с `diag=True` (в проде выключен) — в таблице 90 он учтён, в счёт
84 метод-эндпоинтов под `/api` — нет.

`/docs` и `/openapi.json` (собственные маршруты FastAPI) появляются только
при `ST_DOCS_ENABLED=true` и в реестр не входят.

Файлы-источники: `api_auth.py` (6 путей), `api_series.py` (13),
`api_system.py` (7), `api_media.py` (18), `api_settings.py` (26),
`module.py` (5 служебных + `/api/stream`).

---

## 2. Доступ

Замок — `AuthGateMiddleware` (`modules/gateway/module.py`). Ответ без
установленной сессии: `401 {"authenticated": false, "error": "Требуется вход"}`.

**Публичные** (сессия не нужна):

| Метод | Путь |
|---|---|
| POST | `/api/login` |
| POST | `/api/setup` |
| GET | `/api/auth/status` |
| GET | `/`, `/legacy`, `/v2`, `/v2/`, `/favicon.svg` |
| — | префиксы `/assets/`, `/static/` |

**Все прочие пути требуют сессию.** Сессия — подписанная кука `st_session`
(`same_site=strict`, `http_only`, срок 7 суток, `secure` при
`ST_COOKIE_SECURE=true`). Bearer-токенов и ролей нет: приложение
однопользовательское, любая валидная сессия = администратор. Поэтому
«админ-вкладки» (просмотр БД, логи, отладочные флаги) отдельно не
защищены — они отличаются только тем, что видны в панели.

Отдельные коды внутри хендлеров:

| Код | Когда |
|---|---|
| 400 | отсутствует обязательный параметр тела |
| 403 | таблица БД закрыта (`auth`, `tracker_sessions`); `/api/setup` при уже созданном админе |
| 404 | сериал / медиа-элемент / профиль не найден |
| 409 | задача уже выполняется (переименование, сканирование, нарезка) |
| 429 | IP забанен после 5 неудачных попыток входа за 300 с (бан 300 с) |
| 503 | модуль-владелец не отвечает на запрос по шине |
| 500 | необработанная ошибка; тело ответа — `{"error": "Внутренняя ошибка сервера"}` без трейса |

---

## 3. Карта эндпоинтов

Колонка «Панель» — где вызывается из `web/src`; «—» означает, что из панели
эндпоинт не вызывается (раздел 5).

### 3.1 Авторизация — `api_auth.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/login` | Вход: проверка пароля модулем `auth`, установка сессии | `LoginModal.vue` |
| GET | `/api/auth/status` | Статус онбординга: настроен ли админ, его имя, вошёл ли клиент | `main.ts` |
| POST | `/api/setup` | Первичное создание администратора | `SetupModal.vue` |
| POST | `/api/auth/password` | Смена пароля вошедшим администратором | `SettingsAccount.vue` |
| POST | `/api/logout` | Очистка сессии | `App.vue` |
| GET | `/api/me` | Кто вошёл | — |

### 3.2 Серии — `api_series.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series` | Список сериалов со статусами и счётчиком скачанного | `stores/series.ts` |
| POST | `/api/series` | Создание серии (+ TMDB-маппинг, + раздачи) | `AddSeriesModal.vue` |
| GET | `/api/series/active_torrents` | Прогресс активных загрузок с именами серий | — |
| GET | `/api/series/{series_id}` | Карточка серии (+ `tracker_info` для торрентов, `availability` для web-видео) | `StatusModal.vue`, `StatusComposition.vue`, `StatusVkComposition.vue` |
| POST | `/api/series/{series_id}` | Сохранение свойств: catalog → metadata → library/renaming | `StatusProperties.vue` |
| DELETE | `/api/series/{series_id}` | Удаление серии (каскад по событию) | `App.vue` |
| POST | `/api/series/{series_id}/state` | Эфемерный viewing: открытие/закрытие статус-окна | `stores/ui.ts` |
| POST | `/api/series/{series_id}/toggle_auto_scan` | Вкл/выкл автоскана серии | `App.vue` |
| POST | `/api/series/{series_id}/ignored-seasons` | Список игнорируемых сезонов | `StatusComposition.vue`, `StatusVkComposition.vue` |
| PUT | `/api/series/{series_id}/ignored-episodes` | Память отключённых серий | `StatusComposition.vue` |
| PUT | `/api/series/{series_id}/vk-quality-priority` | Приоритет качества для видео-источника | `StatusVkComposition.vue` |
| GET | `/api/series/{series_id}/torrents/history` | История раздач серии | `StatusHistory.vue`, `StatusProperties.vue` |
| POST | `/api/series/{series_id}/scan` | Синхронный скан одной серии | `App.vue` |

### 3.3 Медиа и файлы — `api_media.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series/{series_id}/media-items` | Медиа-элементы серии | `StatusHistory.vue`, `StatusSlicing.vue` |
| PUT | `/api/media-items/{unique_id}/ignore` | Игнор одного медиа-элемента | `StatusVkComposition.vue` |
| PUT | `/api/series/{series_id}/media-items/ignore-season` | Массовый игнор сезона | `StatusVkComposition.vue` |
| POST | `/api/media-items/{unique_id}/chapters` | Оглавление исходника | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/chapters/filtered` | Оглавление с автофильтром мусора | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/chapters/mark-garbage` | Ручная разметка мусорных глав | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/slice` | Задача нарезки | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/slice-with-filter` | Нарезка с заданным списком отброшенных глав | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/delete-source` | Удаление исходника нарезанной компиляции | `StatusSlicing.vue` |
| POST | `/api/media-items/{unique_id}/verify-sliced-files` | Сверка нарезок с диском | — |
| POST | `/api/series/{series_id}/deep-adoption` | Глубокое усыновление (фон) | `StatusVkComposition.vue` |
| GET | `/api/series/{series_id}/sliced-files` | Нарезки с именем родителя и сезоном | `StatusVkComposition.vue` |
| GET | `/api/series/{series_id}/composition` | Состав композиции | `StatusComposition.vue`, `StatusVkComposition.vue` |
| GET | `/api/series/{series_id}/rename_preview` | Предпросмотр новых имён файлов | `StatusVkComposition.vue` |
| POST | `/api/series/{series_id}/reprocess` | Переобработка имён файлов (торрент) | `StatusComposition.vue` |
| POST | `/api/series/{series_id}/reprocess_vk_files` | Переобработка имён файлов (видео) | `StatusVkComposition.vue` |
| GET | `/api/series/{series_id}/source-filenames` | Имена исходных файлов серии | `ParserTest.vue` |
| POST | `/api/preview/naming/fetch` | Фоновый префетч списков файлов раздач | `AddSeriesModal.vue` |

### 3.4 Скан и очереди — `api_system.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/scanner/status` | Состояние планировщика | `stores/scanner.ts` |
| POST | `/api/scanner/settings` | Настройки планировщика (вкл/выкл, интервал) | `SettingsDebug.vue` |
| POST | `/api/scanner/scan_all` | Полный проход по всем сериалам | `SettingsDebug.vue` |
| GET | `/api/agent/queue` | Очередь торрент-конвейера | — |
| GET | `/api/downloads/queue` | Очередь загрузок видео | — |
| POST | `/api/downloads/queue/clear` | Очистка очереди загрузок | — |
| POST | `/api/downloads/{task_id}/cancel` | Отмена загрузки: процесс + файл + задача | — |

### 3.5 Настройки, справочники, логи, БД — `api_settings.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/auth` | Креды внешних сервисов без секретов + статус TMDB | `SettingsAuth.vue` |
| POST | `/api/auth` | Сохранение кредов сервисов | `SettingsAuth.vue` |
| POST | `/api/parse_url` | Разбор URL серии: заголовок, раздачи, трекер | `AddSeriesModal.vue`, `StatusProperties.vue` |
| GET, POST | `/api/settings/force_replace` | Флаг «всегда заменять торренты» | `SettingsDebug.vue` |
| GET, POST | `/api/settings/less_strict_scan` | Флаг «менее строгий скан» | `SettingsDebug.vue` |
| GET, POST | `/api/settings/slicing_delete_source` | Флаг «удалять исходник после нарезки» | `SettingsDebug.vue` |
| GET, POST | `/api/settings/parallel_downloads` | Число параллельных загрузок yt-dlp | `SettingsDebug.vue` |
| GET, POST | `/api/settings/concurrent_fragments` | Число параллельных фрагментов yt-dlp (`-N`) | `SettingsDebug.vue` |
| GET, POST, DELETE | `/api/settings/saved_paths` | Сохранённые пути загрузки | `SettingsDebug.vue`, `SavedPathDropdown.vue` |
| GET, POST | `/api/settings/debug_flags` | Флаги отладочного логирования | `LogsLoggingTab.vue` |
| POST | `/api/tmdb/search` | Поиск сериала в TMDB | `AddSeriesModal.vue`, `StatusProperties.vue` |
| GET | `/api/tmdb/details/{tmdb_id}` | Детали сериала TMDB | `AddSeriesModal.vue`, `StatusProperties.vue` |
| GET | `/api/trackers` | Список трекеров с зеркалами | `SettingsTrackers.vue` |
| PUT | `/api/trackers/{tracker_id}` | Обновление зеркал трекера | `SettingsTrackers.vue` |
| GET | `/api/directories` | Листинг каталогов | — |
| GET | `/api/parser-profiles` | Список профилей парсера | `SettingsParser.vue`, `AddSeriesModal.vue`, `StatusProperties.vue` |
| POST | `/api/parser-profiles` | Создание профиля | `SettingsParser.vue` |
| PUT | `/api/parser-profiles/{profile_id}` | Переименование профиля | `SettingsParser.vue` |
| DELETE | `/api/parser-profiles/{profile_id}` | Удаление профиля с правилами | `SettingsParser.vue` |
| GET | `/api/parser-profiles/{profile_id}/rules` | Правила профиля | `SettingsParser.vue` |
| POST | `/api/parser-profiles/{profile_id}/rules` | Добавление правила | `SettingsParser.vue` |
| PUT | `/api/parser-rules/{rule_id}` | Изменение правила | `SettingsParser.vue` |
| DELETE | `/api/parser-rules/{rule_id}` | Удаление правила | `SettingsParser.vue` |
| POST | `/api/parser-rules/reorder` | Перестановка правил | `SettingsParser.vue` |
| POST | `/api/parser-profiles/scrape-titles` | Сбор названий с VK-канала или видеохостинга | `ParserTest.vue` |
| POST | `/api/parser-profiles/test` | Тест профиля правил на наборе видео | `ParserTest.vue` |
| GET | `/api/logs` | Чтение логов из `logs/*.log` | `LogsModal.vue` |
| GET | `/api/logs/groups` | Список групп логов (distinct по файлам) | `LogsModal.vue` |
| GET | `/api/database/tables` | Список таблиц БД | `DatabaseViewerModal.vue`, `SettingsDebug.vue` |
| GET | `/api/database/table/{table_name}` | Все строки таблицы | `DatabaseViewerModal.vue` |
| POST | `/api/database/clear_table` | Очистка таблицы | `SettingsDebug.vue` |

### 3.6 Служебные — `module.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/stream` | SSE-поток событий шины | `useSSE.ts` |
| GET | `/` | Боевой фронт (`web/dist/index.html`) | браузер |
| GET | `/legacy` | Старый фронт (откат) | браузер |
| GET | `/v2`, `/v2/` | Редирект 307 на `/` | браузер |
| GET | `/favicon.svg` | Фавикон | браузер |
| POST | `/api/_diag/echo` | Диагностический прокси к шине (только при `diag=True`) | — |

---

## 4. Функции панели → эндпоинты

Полный перечень вызовов из `web/src` — 88 мест (83 прямых `api.METHOD(...)`,
4 через общие хелперы `loadFlag`/`saveFlag`/`loadNum`/`saveNum`, 1 через
`EventSource`), 76 задействованных метод-эндпоинтов. Расхождений «действие
в панели без эндпоинта» нет: каждый вызов попадает в живой маршрут.

| Экран / элемент панели | Метод | Путь |
|---|---|---|
| Старт приложения | GET | `/api/auth/status` |
| Модалка входа | POST | `/api/login` |
| Модалка первичной установки | POST | `/api/setup` |
| Выход | POST | `/api/logout` |
| Галерея: список сериалов | GET | `/api/series` |
| Галерея: сканировать серию | POST | `/api/series/{series_id}/scan` |
| Галерея: тумблер автоскана | POST | `/api/series/{series_id}/toggle_auto_scan` |
| Галерея: удалить серию | DELETE | `/api/series/{series_id}` |
| Открытие статус-окна | POST | `/api/series/{series_id}/state` |
| Окно добавления: разбор URL | POST | `/api/parse_url` |
| Окно добавления: префетч нейминга | POST | `/api/preview/naming/fetch` |
| Окно добавления: поиск в TMDB | POST | `/api/tmdb/search` |
| Окно добавления: детали TMDB | GET | `/api/tmdb/details/{tmdb_id}` |
| Окно добавления: сохранить серию | POST | `/api/series` |
| Окно добавления: список профилей | GET | `/api/parser-profiles` |
| Статус-окно: карточка | GET | `/api/series/{series_id}` |
| Статус-окно: свойства — сохранить | POST | `/api/series/{series_id}` |
| Статус-окно: свойства — разбор URL | POST | `/api/parse_url` |
| Статус-окно: свойства — поиск TMDB | POST | `/api/tmdb/search` |
| Статус-окно: свойства — детали TMDB | GET | `/api/tmdb/details/{tmdb_id}` |
| Статус-окно: свойства — профили | GET | `/api/parser-profiles` |
| Статус-окно: вкладка «История» | GET | `/api/series/{series_id}/media-items` |
| Статус-окно: вкладка «История» | GET | `/api/series/{series_id}/torrents/history` |
| Статус-окно: вкладка «Композиция» | GET | `/api/series/{series_id}/composition` |
| Статус-окно: вкладка «Композиция» | GET | `/api/series/{series_id}` |
| Статус-окно: вкладка «Композиция» — исключить серию | PUT | `/api/series/{series_id}/ignored-episodes` |
| Статус-окно: вкладка «Композиция» — исключить сезон | POST | `/api/series/{series_id}/ignored-seasons` |
| Статус-окно: вкладка «Композиция» — переприменить правила | POST | `/api/series/{series_id}/reprocess` |
| Статус-окно: вкладка «Нарезка» — список элементов | GET | `/api/series/{series_id}/media-items` |
| Статус-окно: вкладка «Нарезка» — оглавление | POST | `/api/media-items/{unique_id}/chapters` |
| Статус-окно: вкладка «Нарезка» — оглавление с фильтром | POST | `/api/media-items/{unique_id}/chapters/filtered` |
| Статус-окно: вкладка «Нарезка» — разметить мусор | POST | `/api/media-items/{unique_id}/chapters/mark-garbage` |
| Статус-окно: вкладка «Нарезка» — нарезать | POST | `/api/media-items/{unique_id}/slice` |
| Статус-окно: вкладка «Нарезка» — нарезать с фильтром | POST | `/api/media-items/{unique_id}/slice-with-filter` |
| Статус-окно: вкладка «Нарезка» — удалить исходник | POST | `/api/media-items/{unique_id}/delete-source` |
| Статус-окно: вкладка «Композиция» (видео) | GET | `/api/series/{series_id}/composition` |
| Статус-окно: вкладка «Композиция» (видео) | GET | `/api/series/{series_id}/rename_preview` |
| Статус-окно: вкладка «Композиция» (видео) | GET | `/api/series/{series_id}` |
| Статус-окно: вкладка «Композиция» (видео) | GET | `/api/series/{series_id}/sliced-files` |
| Статус-окно: вкладка «Композиция» (видео) — приоритет качества | PUT | `/api/series/{series_id}/vk-quality-priority` |
| Статус-окно: вкладка «Композиция» (видео) — игнор элемента | PUT | `/api/media-items/{unique_id}/ignore` |
| Статус-окно: вкладка «Композиция» (видео) — игнор сезона | PUT | `/api/series/{series_id}/media-items/ignore-season` |
| Статус-окно: вкладка «Композиция» (видео) — исключить сезон | POST | `/api/series/{series_id}/ignored-seasons` |
| Статус-окно: вкладка «Композиция» (видео) — переобработать | POST | `/api/series/{series_id}/reprocess_vk_files` |
| Статус-окно: вкладка «Композиция» (видео) — усыновить | POST | `/api/series/{series_id}/deep-adoption` |
| Настройки → Авторизация: прочитать | GET | `/api/auth` |
| Настройки → Авторизация: сохранить | POST | `/api/auth` |
| Настройки → Аккаунт: сменить пароль | POST | `/api/auth/password` |
| Настройки → Трекеры: список | GET | `/api/trackers` |
| Настройки → Трекеры: зеркала | PUT | `/api/trackers/{tracker_id}` |
| Настройки → Правила: профили | GET | `/api/parser-profiles` |
| Настройки → Правила: создать профиль | POST | `/api/parser-profiles` |
| Настройки → Правила: переименовать | PUT | `/api/parser-profiles/{profile_id}` |
| Настройки → Правила: удалить профиль | DELETE | `/api/parser-profiles/{profile_id}` |
| Настройки → Правила: правила профиля | GET | `/api/parser-profiles/{profile_id}/rules` |
| Настройки → Правила: добавить правило | POST | `/api/parser-profiles/{profile_id}/rules` |
| Настройки → Правила: изменить правило | PUT | `/api/parser-rules/{rule_id}` |
| Настройки → Правила: удалить правило | DELETE | `/api/parser-rules/{rule_id}` |
| Настройки → Правила: порядок | POST | `/api/parser-rules/reorder` |
| Настройки → Правила: тест | POST | `/api/parser-profiles/test` |
| Настройки → Правила: собрать названия | POST | `/api/parser-profiles/scrape-titles` |
| Настройки → Правила: имена файлов серии | GET | `/api/series/{series_id}/source-filenames` |
| Настройки → Отладка: настройки сканера | POST | `/api/scanner/settings` |
| Настройки → Отладка: сканировать всё | POST | `/api/scanner/scan_all` |
| Настройки → Отладка: флаги (3 шт.) | GET, POST | `/api/settings/force_replace`, `/api/settings/less_strict_scan`, `/api/settings/slicing_delete_source` |
| Настройки → Отладка: числа (2 шт.) | GET, POST | `/api/settings/parallel_downloads`, `/api/settings/concurrent_fragments` |
| Настройки → Отладка: пути | GET, POST, DELETE | `/api/settings/saved_paths` |
| Настройки → Отладка: список таблиц | GET | `/api/database/tables` |
| Настройки → Отладка: очистить таблицу | POST | `/api/database/clear_table` |
| Просмотр БД: список таблиц | GET | `/api/database/tables` |
| Просмотр БД: содержимое таблицы | GET | `/api/database/table/{table_name}` |
| Логи: список групп | GET | `/api/logs/groups` |
| Логи: записи | GET | `/api/logs` |
| Логи: флаги логирования | GET, POST | `/api/settings/debug_flags` |
| Индикаторы в шапке: статус сканера | GET | `/api/scanner/status` |
| Push-обновления | GET | `/api/stream` (EventSource) |

---

## 5. Эндпоинты, не используемые панелью

8 из 84 метод-эндпоинтов под `/api` панель не вызывает.

| Метод | Путь | Кто вызывает | Статус |
|---|---|---|---|
| GET | `/api/agent/queue` | `legacy_frontend` (вкладка «Агенты») | доступен, но только старому фронту |
| GET | `/api/downloads/queue` | `legacy_frontend` | то же |
| GET | `/api/series/active_torrents` | `legacy_frontend` (вкладка «Мониторинг») | то же |
| POST | `/api/downloads/{task_id}/cancel` | `legacy_frontend` | то же |
| POST | `/api/media-items/{unique_id}/verify-sliced-files` | `legacy_frontend` | то же |
| GET | `/api/directories` | `legacy_frontend` (выбор каталога) | то же |
| POST | `/api/downloads/queue/clear` | только тесты | админ-инструмент, в панели нет кнопки |
| GET | `/api/me` | только тесты | панель определяет вход по `/api/auth/status` |

---

## 6. Контракты

Тела запросов разбираются вручную (`await request.json()`), схем запросов нет;
формы ответов заданы `response_model` в `schemas.py` (база `extra="allow"` —
лишние поля не отрезаются). Поля `ignored_seasons`, `ignored_episodes`,
`vk_quality_priority` передаются **строкой JSON**, а не объектом.

### 6.1 Авторизация

**POST `/api/login`** — тело `{username, password}`; ответ
`{success, username}`; ошибки: 401 (неверные данные), 429 (бан IP), 503
(модуль `auth` не отвечает), 500 (тело не JSON).

**GET `/api/auth/status`** — ответ `{configured, authenticated, username, admin}`.

**POST `/api/setup`** — тело `{username, password}` (пароль ≥ 8 символов);
ответ `{success, username}`; ошибки: 400 (пустое имя или короткий пароль),
403 (администратор уже создан), 503.

**POST `/api/auth/password`** — тело `{new}` (≥ 8 символов); ответ
`{success}`; ошибки: 400 (короткий пароль), 401 (нет сессии). Текущий пароль
не запрашивается: активная сессия подтверждает владельца.

**POST `/api/logout`** — ответ `{success}`.

**GET `/api/me`** — ответ `{authenticated, username}`; 401 без сессии.

### 6.2 Серии

**GET `/api/series`** — список объектов серии. Постоянные поля: `id`, `name`,
`name_en`, `site`, `url`, `save_path`, `source_type`, `vk_search_mode`,
`auto_scan_enabled`, `is_busy`, `statuses`, `last_scan_time`, `tmdb_info`;
непостоянные — `season`, `quality`, `quality_override`,
`resolution_override`, `parser_profile_id`, `ignored_seasons`,
`ignored_episodes`, `vk_quality_priority`, `downloaded_episodes_count`.
`tmdb_info` — маппинг или `null`; `downloaded_episodes_count` — целое (0, если
модуль недоступен).

**POST `/api/series`** — тело передаётся в `catalog.series.create` целиком.
Обязательные: `url`, `name`, `name_en`, `site`, `save_path`. Необязательные:
`source_type` (`torrent` | `vk_video` | `web_video`, по умолчанию `torrent`),
`auto_scan_enabled` (false), `vk_search_mode` (`search`), `season`, `quality`,
`quality_override`, `resolution_override`, `parser_profile_id`,
`ignored_seasons` (`"[]"`), `ignored_episodes` (`"{}"`),
`vk_quality_priority` (null), `tmdb_data`, `torrents`.
`tmdb_data` — объект `{tmdb_id, tmdb_season_number, total_episodes,
poster_path, series_name, year}`; `torrents` — список
`{torrent_id, link, date_time, quality, episodes}`, `link` обязателен.
Ответ `{success, series_id}`. Ошибка записи раздач не влияет на ответ: она
логируется как предупреждение, серия создаётся без них.

**GET `/api/series/{series_id}`** — объект серии плюс `tracker_info` (только
для `source_type = "torrent"`). Ошибки: 404 (не найден), 400, 409, 500.

**POST `/api/series/{series_id}`** — все поля необязательные; меняются только
переданные. `save_path` идёт не в `catalog`, а в `library.relocate`;
`tmdb_data` — в `metadata.map.set`; `last_scan_time` игнорируется. Если путь
не изменился и источник не видео — команда `renaming.reprocess`. Ответ
`{success, message}`.

**DELETE `/api/series/{series_id}`** — query `delete_from_qb` (bool, по
умолчанию false) — снять раздачи и в qBittorrent. Ответ `{success}`.

**POST `/api/series/{series_id}/state`** — тело `{state: ["viewing"]}` при
открытии статус-окна, `{state: []}` при закрытии. Открытие дополнительно
запускает сверку файлов с диском. Ответ `{success}`.

**POST `/api/series/{series_id}/toggle_auto_scan`** — тело `{enabled}`
(обязательно, иначе 400).

**POST `/api/series/{series_id}/ignored-seasons`** — тело `{seasons: [...]}`;
сохраняется как JSON-строка. 400 при отсутствии ключа.

**PUT `/api/series/{series_id}/ignored-episodes`** — тело
`{episodes: {"<сезон>": [номера]}}`. Ключ — номер серии сериала, а не файл
раздачи: выбор обязан пережить замену релиза. 400 при отсутствии ключа.

**PUT `/api/series/{series_id}/vk-quality-priority`** — тело `{priority: [...]}`;
обязателен и должен быть списком, иначе 400. Ответ `{success, message}`.

**GET `/api/series/{series_id}/torrents/history`** — список раздач: `id`,
`series_id`, `torrent_id`, `link`, `date_time`, `quality`, `episodes`,
`is_active`, `qb_hash`. При недоступном модуле — пустой список.

**POST `/api/series/{series_id}/scan`** — синхронный: HTTP ждёт результата
(таймаут шины 900 с). Ответ `{success, changed, tasks_created}` для торрента,
сводка для видео. 409 — сканирование уже идёт.

### 6.3 Медиа и файлы

**GET `/api/series/{series_id}/media-items`** — список элементов: `id`,
`series_id`, `unique_id`, `source_title`, `season`, `episode_start`,
`episode_end`, `plan_status`, `status`, `is_ignored_by_user`, `source_url`,
`publication_date` (ISO), `voiceover_tag`, `final_filename`, `chapters`,
`chapters_filtered`, `slicing_status`, `is_available`, `resolution`, `part`.

**PUT `/api/media-items/{unique_id}/ignore`** — тело `{is_ignored}`
(обязательно, иначе 400).

**PUT `/api/series/{series_id}/media-items/ignore-season`** — тело
`{season, is_ignored}` (оба обязательны, иначе 400). Снимок списка
элементов — на момент вызова.

**POST `/api/media-items/{unique_id}/chapters`** — список глав
`{title, start_time, end_time}`. 404 — элемент не найден, 500 — не удалось
получить.

**POST `/api/media-items/{unique_id}/chapters/filtered`** — ответ
`{chapters, filtered_chapters, garbage_chapters, expected_count,
status_message}`.

**POST `/api/media-items/{unique_id}/chapters/mark-garbage`** — тело
`{garbage_indices: [...]}`; ответ — как у `chapters/filtered`.

**POST `/api/media-items/{unique_id}/slice`** — ответ `{success, message}`;
если исходника нет — 200 с `{success: false, source_missing: true, message}`.
409 — задача уже в очереди, 400 — число глав не совпало.

**POST `/api/media-items/{unique_id}/slice-with-filter`** — тело
`{garbage_indices: [...]}`; ответ добавляет `filtered_chapters_count`.

**POST `/api/media-items/{unique_id}/delete-source`** — ответ
`{success, deleted, message}`; `deleted: false`, если файла уже нет.

**POST `/api/media-items/{unique_id}/verify-sliced-files`** — ответ
`{status, message, skipped}`; `status` — `none` | `completed` |
`completed_with_errors` | `error`.

**POST `/api/series/{series_id}/deep-adoption`** — фоновая операция; ответ
`{success, message}` сразу.

**GET `/api/series/{series_id}/sliced-files`** — список нарезок: `id`,
`series_id`, `source_media_item_unique_id`, `episode_number`, `file_path`,
`status`, `parent_filename`, `season`.

**GET `/api/series/{series_id}/composition`** — query `refresh` (bool) — полное
обновление; для торрента дополнительно выполняется `scan.series.prepare`.
Список элементов композиции; 404 — сериал не найден.

**GET `/api/series/{series_id}/rename_preview`** — ответ
`{preview: [{unique_id, type, current_filename, new_filename_preview}],
needs_rename_count}`; без профиля парсера — пустой превью.

**POST `/api/series/{series_id}/reprocess`** и
**`/reprocess_vk_files`** — ответ `{success, message}`; 409 — переименование
уже выполняется.

**GET `/api/series/{series_id}/source-filenames`** — список строк с базовыми
именами файлов.

**POST `/api/preview/naming/fetch`** — тело `{items: [{torrent_id, magnet?,
link?}]}`; ответ сразу `{accepted: [torrent_id, ...]}`, сами списки файлов
приходят по SSE (`preview_naming_ready` / `preview_naming_failed`).

### 6.4 Скан и очереди

**GET `/api/scanner/status`** — `{scanner_enabled, is_scanning,
is_awaiting_tasks, scan_interval, next_scan_time}`.

**POST `/api/scanner/settings`** — тело `{enabled?, interval?}`; каждый
переданный ключ пишется отдельно (`scanner_agent_enabled`,
`scan_interval_minutes`), пересчёт расписания делает модуль `scan` по
событию `settings.changed`.

**POST `/api/scanner/scan_all`** — тело необязательно; `debug_force_replace`
переключает замену существующих раздач. Ответ `{success, message}`; 409 — скан
уже идёт.

**GET `/api/agent/queue`** — список задач конвейера, у каждой добавлено поле
`hash`.

**GET `/api/downloads/queue`** — список задач загрузки: `id`, `task_key`,
`series_id`, `video_url`, `save_path`, `status`, `error_message`, `attempts`,
`created_at`, `updated_at`, `task_type`, `progress`, `dlspeed`, `eta`,
`total_size_mb`.

**POST `/api/downloads/queue/clear`** — ответ `{success, message}` с числом
удалённых задач.

**POST `/api/downloads/{task_id}/cancel`** — убивает процесс, удаляет файл,
снимает задачу; ответ `{success, message}`; 404 — задача не найдена.

### 6.5 Настройки, логи, БД

**GET `/api/auth`** — по каждому сервису (`qbittorrent`, `kinozal`, `vk`,
`rutracker`) — `{username, url, has_password}`, секреты не возвращаются;
`tmdb` — `{configured}`. Если кредов нет — пустой объект.

**POST `/api/auth`** — тело `{qbittorrent?, kinozal?, rutracker?, vk?,
tmdb?}`; каждый сервис — объект `{username, password?, url?}`, пустой пароль
означает «не менять». Для `vk` токен кладётся в поле `password`; `tmdb`
пишется, только если токен непустой.

**POST `/api/parse_url`** — тело `{url}`; ответ `{success, title, torrents,
tracker_info, source_type, titles, canonical_url, episodes_count,
resolution}`. 400 — трекер не распознан; 500 — в теле нет ключа `url`.

**GET|POST `/api/settings/force_replace`**, **`/less_strict_scan`**,
**`/slicing_delete_source`** — тело `{enabled}`; ответ GET `{enabled}`,
POST `{success}`. Ключи в БД: `debug_force_replace`,
`debug_less_strict_scan`, `slicing_delete_source_file`.

**GET|POST `/api/settings/parallel_downloads`** и
**`/concurrent_fragments`** — тело `{value}`; ответ GET `{value}` (fallback 2
и 6 соответственно), POST `{success}`. Ключи: `max_parallel_downloads`,
`yt_dlp_concurrent_fragments`.

**GET|POST|DELETE `/api/settings/saved_paths`** — GET: `{paths: [{id, path}]}`;
POST: тело `{path}` (пустая строка игнорируется); DELETE: тело `{id}` — id
передаётся в теле, не в query. Ответ — актуальный список `{paths}`.

**GET|POST `/api/settings/debug_flags`** — GET:
`{logging_modules: {<группа>: [{name, description, enabled}]},
file_dump_flags: [{name, description, enabled}]}`; POST: тело `{module,
enabled}` — `module` обязателен (иначе 400).

**POST `/api/tmdb/search`** — тело `{query}`; ответ `{success, results}`; 400 —
запрос пуст.

**GET `/api/tmdb/details/{tmdb_id}`** — ответ `{success, name, poster_path,
status, seasons: [{season_number, episode_count, air_date, name}]}`; 404 —
сериал не найден в TMDB.

**GET `/api/trackers`** — список: `id`, `canonical_name`, `display_name`,
`mirrors`, `parser_class`, `auth_type`, `ui_features`.

**PUT `/api/trackers/{tracker_id}`** — тело `{mirrors}` (обязательно, иначе
400); ответ `{success, message}`.

**GET `/api/directories`** — query `path` (по умолчанию `/`); ответ
`{path, items: [{name, path, type}]}`; 403 — нет доступа, 400 — не каталог.

**GET `/api/parser-profiles`** — список `{id, name, preferred_voiceovers}`.

**POST `/api/parser-profiles`** — тело `{name}`; ответ **201** `{success, id}`;
400 — имя не указано, 409 — профиль с таким именем уже есть.

**PUT `/api/parser-profiles/{profile_id}`** — тело `{name}`; 400 — новое имя
не указано, 409 — дубль имени либо профиля нет.

**DELETE `/api/parser-profiles/{profile_id}`** — ответ `{success}`; 400 —
профиль используется сериалами.

**GET `/api/parser-profiles/{profile_id}/rules`** — список правил: `id`,
`profile_id`, `name`, `priority`, `action_pattern`,
`continue_after_match`, `conditions: [{id, condition_type, pattern,
logical_operator}]`, плюс `valid` (bool) и `error` (строка причины или
`null`). `valid: false` — правило неисправно и отключено: оно не
применяется, а `error` объясняет почему.

**POST `/api/parser-profiles/{profile_id}/rules`** — тело — данные правила:
`name` (по умолчанию «Новое правило»), `action_pattern` (JSON-шаблон, по
умолчанию `"[]"`), `continue_after_match` (false), `conditions` (список
`{condition_type, pattern, logical_operator?}`). Ответ `{success, id}`.
400 — тело не проходит проверку словаря и правил размещения блоков
(раздел 6.6).

**PUT `/api/parser-rules/{rule_id}`** — тело — данные правила; отсутствующие
поля сохраняют прежние значения. `conditions` — то же правило: если поле не
прислано, условия сохраняются; удалить их можно, прислав пустой список
`[]`. 400 — тело не проходит проверку словаря и правил размещения блоков.

**DELETE `/api/parser-rules/{rule_id}`** — ответ `{success}`.

**POST `/api/parser-rules/reorder`** — тело — **массив** id правил в новом
порядке (не объект). Ответ `{success}`.

**POST `/api/parser-profiles/scrape-titles`** — тело `{channel_url, query?,
search_mode?}`; `search_mode` — `search` | `get_all` (только VK). Ответ —
список `{title, url, publication_date, resolution}`. 400 — пустая ссылка,
указано отдельное видео, указан трекер или в URL канала нет имени `@…`.

**POST `/api/parser-profiles/test`** — тело `{profile_id, videos}`; `videos` —
список объектов с полем `title`. Ответ — список
`{source_data, match_events, result: {extracted, excluded}}`. 400 — не задан
`profile_id` или пуст `videos`.

**GET `/api/logs`** — query `group` (имя модуля), `level` (`error` |
`warning` | `info` | `debug`), `limit` (по умолчанию 200). Ответ — список
`{timestamp, level, group, message}`, свежие сверху. Файлы читаются напрямую
из `logs/*.log`, минуя шину.

**GET `/api/logs/groups`** — список строк — имена групп, реально встреченные
в логах.

**GET `/api/database/tables`** — список имён таблиц; `auth` и
`tracker_sessions` исключены.

**GET `/api/database/table/{table_name}`** — список строк таблицы; 403 — для
закрытой или неизвестной таблицы.

**POST `/api/database/clear_table`** — тело `{table_name}`; ответ
`{success, message}`; 400 — имя не указано, 500 — таблица закрыта или не
существует.

### 6.6 Конструктор правил: устройство и словарь

Состояние конструктора целиком хранится через четыре эндпоинта:

| Операция в конструкторе | Метод | Путь |
|---|---|---|
| Создать правило | POST | `/api/parser-profiles/{profile_id}/rules` |
| Изменить правило | PUT | `/api/parser-rules/{rule_id}` |
| Удалить правило | DELETE | `/api/parser-rules/{rule_id}` |
| Изменить порядок правил | POST | `/api/parser-rules/reorder` |

Перетаскивание блоков внутри шаблона (`vuedraggable` в
`ParserPatternEditor.vue`) — операция только на стороне клиента: блоки
собираются в список, при сохранении сериализуются в `pattern` условия или в
`action_pattern` действия и уходят в тело `POST`/`PUT` правила. Порядок
правил в панели меняется стрелками «вверх/вниз» (`moveRule`), и уходит в
`POST /api/parser-rules/reorder` массивом id. Перетаскивание приоритета
качества в статус-окне видео-сериала уходит в
`PUT /api/series/{series_id}/vk-quality-priority`.

**`condition_type`** — «ЕСЛИ». Значения, которые предлагает панель:

| Значение | Смысл |
|---|---|
| `contains` | шаблон найден в названии |
| `not_contains` | шаблон не найден |

Список закрыт: значение вне `contains` / `not_contains` отклоняется при
записи правила.

**`pattern`** — JSON-**строка** со списком блоков шаблона:

```json
[{"type": "text", "value": "S01"},
 {"type": "number"},
 {"type": "range", "value": "1-180"}]
```

| Тип блока | Значение | Где допустим |
|---|---|---|
| `text` | точная подстрока (`value`) | ЕСЛИ, ТО |
| `number` | любое число | ЕСЛИ, ТО |
| `range` | диапазон номеров: `1`, `3-10`, `1-180` (`value`) | **только ЕСЛИ** |
| `add` | прибавить `value` к предыдущему числу | **только ТО** |
| `subtract` | вычесть `value` из предыдущего числа | **только ТО** |
| `whitespace` | один или несколько пробелов | ЕСЛИ, ТО |
| `any_text` | любой текст (нежадный поиск) | ЕСЛИ, ТО |
| `start_of_line` | привязка к началу названия | ЕСЛИ, ТО |
| `end_of_line` | привязка к концу названия | ЕСЛИ, ТО |

**Блоки-приложения** (`range`, `add`, `subtract`) — хвосты числа: они
фильтруют или меняют число, за которым стоят. Правила одинаковы для всех
трёх видов:

- блок-приложение **один на шаблон**;
- в шаблоне с блоком-приложением **ровно одно число**, и блок стоит сразу за
  ним — иначе он указывал бы на неоднозначное число;
- числа в шаблоне нет — блок-приложение недопустим;
- `range` допустим только в ЕСЛИ, `add`/`subtract` — только в ТО.

Нарушение — ошибка правила: `POST`/`PUT` отвечают 400 с текстом причины, а
невалидное правило помечается в списке профиля (`valid: false`, `error`).

**Панель и API ограничены одинаково.** В панели это видно прямо:
недопустимый блок в палитре недоступен (серым цветом, с причиной в
подсказке), и его нельзя перетащить. Блок-приложение пристёгивается к
единственному числу сам и рисуется единым телом с ним.

Смысл «Диапазона» при одном числе: он фильтрует это число («серия не в
1-180»), а компиляция «10-30» определяется по самому названию. Явный шаблон
компиляции из двух чисел с «Диапазоном» недопустим — ровно тот случай, где
блок указывал бы сразу на два числа.

**`action_pattern`** — JSON-**строка** со списком действий, каждое действие
— `{action_type, action_pattern}`; вложенный `action_pattern` — снова
JSON-строка со списком блоков:

```json
[{"action_type": "extract_single", "action_pattern": "[{\"type\":\"number\"}]"},
 {"action_type": "assign_voiceover", "action_pattern": "LostFilm"}]
```

| `action_type` | Смысл | `action_pattern` |
|---|---|---|
| `exclude` | исключить видео | пустая строка |
| `extract_single` | извлечь номер серии | блоки (`number`) |
| `extract_range` | извлечь диапазон серий | блоки |
| `extract_season` | установить номер сезона | блоки |
| `extract_part` | извлечь часть серии | блоки — принимается модулем, но не предлагается панелью |
| `assign_voiceover` | назначить озвучку/тег | строка значения |
| `assign_episode` | назначить номер серии | строка значения |
| `assign_season` | назначить номер сезона | строка значения |
| `assign_part` | назначить часть серии | строка значения |
| `assign_quality` | назначить качество | строка значения |
| `assign_resolution` | назначить разрешение | строка значения |

Список закрыт: `assign_*` принимается только из шести перечисленных выше,
`extract_*` — только из четырёх. Любой другой тип отклоняется при записи
правила.

**`priority`** — поле присутствует в ответе `GET
/api/parser-profiles/{profile_id}/rules` и определяет порядок применения
правил. Задать его напрямую нельзя: `POST` правила присваивает
`priority = max + 1`, `PUT` правила поле не трогает. Порядок меняется
только через `POST /api/parser-rules/reorder`, который проставляет
`priority` равным индексу в переданном массиве.

**Приоритет правил над карточкой.** Правило, сработавшее на конкретном
файле, важнее значения, заданного в карточке сериала. Действует для
качества, разрешения и номера сезона.

| Что задано | Что попадёт в имя файла |
|---|---|
| Правило назначило значение | значение правила |
| Правило не назначило, в карточке есть «Качество (ручной ввод)» / «Разрешение (ручной ввод)» | значение карточки |
| Ни правила, ни карточки | факт из БД |

Сезон резолвится отдельно и по тому же принципу: извлечённый правилами
сезон побеждает, сезон карточки — дефолт, когда правило ничего не дало
(`core/season.py`).


отклоняют некорректное с кодом 400 и текстом причины. Проверка выполняется
тем же компилятором, что и применение профиля, — отдельного валидатора нет,
чтобы проверка и исполнение не разошлись. Отклоняются: неизвестный
`condition_type`, неизвестный `action_type`, неизвестный тип блока, блок
`range` в действии «ТО», блок `±` в условии «ЕСЛИ», два блока-приложения в
одном шаблоне, блок-приложение не сразу за единственным числом, нечисловое
значение блока `±`, пустой шаблон, некорректный JSON в `action_pattern`.

```
POST /api/parser-profiles/{profile_id}/rules
400 {"success": false, "error": "неизвестный тип действия: 'assign_voiceoverr'"}
```

Правило, сломанное до появления проверки (или исправленное в базе вручную),
при применении профиля отключается с записью причины в лог модуля `rules`,
а не роняет профиль. Проверка без записи в базу доступна через
`POST /api/parser-profiles/test`.

---

## 7. Поток событий SSE

Один эндпоинт — `GET /api/stream`, `text/event-stream`. Кадр:
`event: <имя>\ndata: <json>\n\n`; каждые 15 с — keepalive-комментарий. Топики
шины, не попавшие в `SSE_MAP`, в поток не передаются.

| Событие SSE | Топик шины | Payload |
|---|---|---|
| `series_updated` | `series.status.changed` | `{id, statuses, is_busy}` |
| `series_updated` | `series.busy.changed` | `{id, statuses, is_busy}` |
| `series_updated` | `series.downloaded.changed` | дельта счётчика скачанного |
| `series_updated` | `series.updated` | дельта изменённых полей |
| `series_added` | `series.added` | полный объект серии |
| `series_deleted` | `series.deleted` | `{id}` |
| `agent_queue_update` | `torrents.queue.changed` | массив задач |
| `torrent_progress_update` | `torrents.progress.changed` | массив задач |
| `preview_naming_ready` | `torrents.preview.ready` | результат префетча |
| `preview_naming_failed` | `torrents.preview.failed` | `{torrent_id, infohash, reason}` |
| `download_queue_update` | `downloads.queue.changed` | массив задач |
| `slicing_queue_update` | `slicing.queue.changed` | массив задач |
| `scanner_status_update` | `scan.status.changed` | статус сканера |
| `renaming_complete` | `renaming.finished` | `{series_id}` |
| `relocation_started` | `library.relocation.started` | `{series_id}` |
| `relocation_finished` | `library.relocation.finished` | `{series_id, success, message}` |

Подробный контракт событий — [sse_contract.md](sse_contract.md).

---

## 8. Спецификация и типы фронта

Машиночитаемая спецификация в репозитории не хранится: `openapi.json`
генерируется на лету и в проде отключён (`ST_DOCS_ENABLED` не задан), а
`/docs` и `/openapi.json` дополнительно перекрыты замком — на проде отдают 401.

```bash
# перегенерировать реестр endpoints.json из живого приложения
.venv/bin/python scripts/gen_endpoints.py

# поднять приложение с включённой документацией
ST_DOCS_ENABLED=true .venv/bin/uvicorn run:app --port 5000

# снять спецификацию
curl -s http://127.0.0.1:5000/openapi.json -o /tmp/openapi.json

# перегенерировать типы фронта
cd web && npm run gen:api
```

Типы фронта лежат в `web/src/api/schema.d.ts`; они выровнены с кодом.
Формы ответов 25 read-эндпоинтов зафиксированы golden-харнессом
`tests/api_golden.py` (снимки в `tests/golden/api/`, вне git).

---

## 9. Расхождения с реестром v1

Реестр от 2026-06-11 описывал 77 метод-эндпоинтов модуля `routes/` (Flask),
которого в системе v2 нет. Сопоставление с текущим кодом: 89
метод-эндпоинтов в продуктовой конфигурации, из них **68 совпадают** по пути.

**Не были описаны — 17 метод-эндпоинтов под `/api`:**

`POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET /api/auth/status`,
`POST /api/auth/password`, `POST /api/setup`, `POST
/api/downloads/{task_id}/cancel`, `GET /api/logs/groups`, `POST
/api/media-items/{unique_id}/delete-source`, `POST /api/preview/naming/fetch`,
`PUT /api/series/{series_id}/ignored-episodes`, `PUT
/api/series/{series_id}/media-items/ignore-season`, `GET /api/settings/
concurrent_fragments`, `POST /api/settings/concurrent_fragments`, `GET
/api/settings/saved_paths`, `POST /api/settings/saved_paths`, `DELETE
/api/settings/saved_paths`.

Плюс 4 служебных пути, которых в реестре не было вовсе: `/favicon.svg`,
`/legacy`, `/v2`, `/v2/`.

**Были описаны, но отсутствуют в коде — 9:**

`POST /api/agent/reset`, `POST /api/database/clear`, `GET /api/hello-info`,
`PUT /api/media-items/{item_id}/ignore` (дубль по числовому id),
`POST /api/series/{series_id}/relocate`, `POST
/api/series/{series_id}/reset_torrents`, `POST
/api/series/{series_id}/viewing_heartbeat`, `GET /directory-picker-test`,
`GET /hello-world`.

У совпадающих 68 колонки «Обработчик» и «Где» указывали на несуществующие
файлы `routes/*.py`; в этом документе — на действующие
`modules/gateway/api_*.py`.

Четыре из девяти удалённых путей остались в тестах: `/api/agent/reset`
(`tests/test_system_api.py`), `/api/series/{series_id}/reset_torrents` и
`/api/series/{series_id}/relocate` (`tests/test_media_api.py`),
`/api/database/clear` (`tests/test_settings_api.py`). Ещё одна ссылка —
`legacy_frontend/templates/hello_world.html` обращается к
`GET /api/hello-info`, которого нет: запрос даёт 404.
