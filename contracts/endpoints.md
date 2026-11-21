# HTTP API — справочник (v2)

> Актуально на **2026-11-21** (этап 5д миссии modular-architecture; реестр `endpoints.json` пересоздан `scripts/gen_endpoints.py`). Источник истины — код
> `modules/gateway/api_*.py`; перечень снят с живого приложения
> (`GatewayModule._create_app`), а не из документации.
>
> Документ заменил реестр v1 от 2026-06-11, который описывал Flask-блюпринты
> `routes/*.py` — файлов этого модуля в системе v2 нет.

---

## 1. Объём

| | |
|---|---|
| Уникальных путей | 100 |
| Метод-эндпоинтов (путь × метод) | 120 |
| Путей под `/api` | 95 |
| Метод-эндпоинтов под `/api` | 115 |
| Служебных путей вне `/api` | 4 в реестре: `/`, `/legacy`, `/v2`, `/v2/`; `/favicon.svg` отдаётся при собранном `web/dist` |

Один маршрут условный: `POST /api/_diag/echo` существует только при запуске
gateway с `diag=True` (в проде выключен) — в таблице 120 он учтён, в счёт
115 метод-эндпоинтов под `/api` — нет.

`/docs` и `/openapi.json` (собственные маршруты FastAPI) появляются только
при `ST_DOCS_ENABLED=true` и в реестр не входят.

Файлы-источники: `api_auth.py` (6), `api_media.py` (2), `api_output.py` (12), `api_series.py` (10), `api_settings.py` (29), `api_sources.py` (10), `api_system.py` (13), `api_torrents.py` (12),
`module.py` (6: 4 служебных пути, `/api/stream` и диагностический).

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

Колонка «Панель» — где вызывается из `web/src` (по `panel_sites` реестра `endpoints.json`); «—» означает, что из панели
эндпоинт не вызывается (раздел 5).

### 3.1 Авторизация — `api_auth.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/login` | Вход: проверка пароля модулем `auth`, установка сессии | `LoginModal.vue` |
| GET | `/api/auth/status` | Статус онбординга: настроен ли админ, его имя, вошёл ли клиент | `useRealtime.ts`, `main.ts` |
| POST | `/api/setup` | Первичное создание администратора | `SetupModal.vue` |
| POST | `/api/auth/password` | Смена пароля вошедшим администратором | `SettingsAccount.vue` |
| POST | `/api/logout` | Очистка сессии | `App.vue` |
| GET | `/api/me` | Кто вошёл | — |

### 3.2 Серии — `api_series.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series` | Список сериалов: `sources`, статусы, счётчик скачанного | `series.ts` |
| POST | `/api/series` | Создание серии с источниками (минимум один; + TMDB-маппинг, + раздачи торрента) | `AddSeriesModal.vue` |
| GET | `/api/series/active_torrents` | Прогресс активных загрузок с именами серий | — |
| GET | `/api/series/{series_id}` | Карточка серии: `sources`, `tmdb_info`, `planned_save_path` | `StatusModal.vue` |
| POST | `/api/series/{series_id}` | Сохранение свойств серии (без источников): catalog → metadata → library/renaming | `StatusProperties.vue` |
| DELETE | `/api/series/{series_id}` | Удаление серии (каскад по событию) | `App.vue` |
| POST | `/api/series/{series_id}/state` | Эфемерный viewing: открытие/закрытие статус-окна | `ui.ts` |
| POST | `/api/series/{series_id}/toggle_auto_scan` | Вкл/выкл автоскана серии | `App.vue` |
| POST | `/api/series/{series_id}/ignored-seasons` | Список игнорируемых сезонов | — |
| PUT | `/api/series/{series_id}/ignored-episodes` | Память отключённых серий | — |
| POST | `/api/series/{series_id}/scan` | Синхронный скан одной серии | `App.vue`, `outputCalls.ts` |
| POST | `/api/series/{series_id}/failures/reset` | Забыть неудачные попытки сериала: все или одного уровня (сезон, серия, озвучка, нарезки) | `outputCalls.ts` |
| POST | `/api/series/{series_id}/scan/cancel` | Остановить синхронизацию сериала: чтение страниц, загрузки, задачи выравнивания, сборку файлов | `App.vue`, `outputCalls.ts` |

### 3.3 Медиа и файлы — `api_media.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/series/{series_id}/reprocess` | Переобработка имён файлов (торрент) | — |
| GET | `/api/series/{series_id}/source-filenames` | Имена файлов раздач торрент-сериала для теста правил | `rulesTestCalls.ts` |

### 3.4 Скан и очереди — `api_system.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/scanner/status` | Состояние планировщика | `scanner.ts` |
| POST | `/api/scanner/settings` | Настройки планировщика (вкл/выкл, интервал) | `ScheduleSettings.vue` |
| POST | `/api/scanner/scan_all` | Полный проход по всем сериалам | `ScheduleSettings.vue` |
| GET | `/api/agent/queue` | Очередь торрент-конвейера | — |
| GET | `/api/downloads/queue` | Очередь загрузок видео | — |
| POST | `/api/downloads/queue/clear` | Очистка очереди загрузок | — |
| POST | `/api/downloads/{task_id}/cancel` | Отмена загрузки: процесс + файл + задача; допустима у задачи в очереди, идущей и обрабатываемой, иначе 409 с причиной `not_allowed` | — |
| DELETE | `/api/downloads/cards/{card_id}` | Убрать карточку загрузки, оставшуюся после синхронизации | `AgentsPanel.vue` |
| DELETE | `/api/pack/cards/{card_id}` | Убрать карточку упаковки, оставшуюся после синхронизации | `AgentsPanel.vue` |
| GET | `/api/pack/queue` | Очередь упаковки: карточка на контейнер, который собирает синхронизация, его стадии и дорожки | — |
| POST | `/api/schedule` | Времена суток синхронизаций и проверок торрент-раздач; расписание, которое scan отвергает, — отказ 400 с причиной | `ScheduleSettings.vue` |

### 3.5 Настройки, справочники, логи, БД — `api_settings.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/auth` | Креды внешних сервисов без секретов + статус TMDB | `SettingsAuth.vue` |
| POST | `/api/auth` | Сохранение кредов сервисов | `SettingsAuth.vue` |
| GET | `/api/auth/{service}/secret` | Секрет одного сервиса по просьбе глаза его поля (не кэшируется); пустой, если не задан | `SettingsAuth.vue` |
| GET, POST | `/api/settings/conform_clear_finished` | Флаг «очищать очередь выравнивания по завершении» (Р-59): завершённая задача уходит из истории демона вместе с графиками | `ExtensionConform.vue` |
| GET, POST, DELETE | `/api/settings/saved_paths` | Сохранённые пути загрузки | `SavedPathDropdown.vue`, `SettingsDebug.vue` |
| GET, POST | `/api/settings/debug_flags` | Флаги отладочного логирования | `LogsLoggingTab.vue` |
| GET | `/api/voices/settings` | Группы озвучек и порядок источников (`VoiceSettingsView`): как действуют сейчас, порядок по умолчанию, отвергнутые сохранённые значения (Р-54) | `SettingsVoices.vue` |
| GET | `/api/conform/settings` | Адрес демона выравнивания, рабочая папка здесь и на демоне, очистка очереди по завершении (`ConformSettingsView`): как действуют сейчас (Р-57, Р-59) | `ExtensionConform.vue` |
| DELETE | `/api/conform/jobs/{job_id}` | Остановить задачу выравнивания, которая ждёт или идёт (запись остаётся со статусом «отменено»), либо удалить запись завершённой: демон снимает свои файлы записи, выходной звук остаётся; ответ `{done: true}`, демон недоступен — 502 с кодом `conform_job_unchanged` (Р-59) | `AgentsPanel.vue` |
| GET | `/api/conform/jobs/{job_id}/plot/{name}` | Картинка графика выравнивания (PNG), как её сделал демон; допускается только простое имя `*.png`; нет графика — 404 `conform_plot_missing`, демон недоступен — 502 (Р-59) | `work.ts` |
| GET | `/api/provider` | Поставщик внешних источников (Chines) как он есть (`ProviderView`): адрес, признак токена, состояние розетки и причина, манифест (id, версия, протокол, ревизия), сайты поставщика с числом источников и линий, папка загрузок | `ExtensionChines.vue`, `provider.ts` |
| PUT | `/api/provider` | Адрес и токен целиком (`ProviderSettingsDraft` {url, token?}); адрес применяется сразу; токен не задан — прежний остаётся, пустой — удаляется; ответ `ProviderSaved`: `saved: true` и картина либо `saved: false` и коды причин (`provider_url_invalid`) | `ExtensionChines.vue` |
| PUT | `/api/conform/settings` | Адрес и рабочая папка здесь и на демоне целиком (`ConformSettingsDraft` {url, work_dir, work_dir_remote}); при заданном адресе обе папки обязательны; ответ `ConformSettingsSaved`: `saved: true`, новый адрес применён сразу, либо `saved: false` и коды причин, ничего не записано (Р-57, Р-58) | `ExtensionConform.vue` |
| PUT | `/api/voices/settings` | Группы и порядок целиком (`VoiceSettingsDraft` {priority, voice_groups}); ответ `VoiceSettingsSaved`: `saved: true` и действующие значения либо `saved: false` и коды причин, ничего не записано. Экран шлёт каждое действие сразу (Р-54) | `SettingsVoices.vue` |
| POST | `/api/tmdb/search` | Поиск сериала в TMDB | `useTmdbChoice.ts` |
| GET | `/api/tmdb/details/{tmdb_id}` | Детали сериала TMDB | `StatusProperties.vue`, `useTmdbChoice.ts` |
| GET | `/api/trackers` | Список трекеров с зеркалами | — |
| PUT | `/api/trackers/{tracker_id}` | Обновление зеркал трекера | `SettingsSources.vue` |
| GET | `/api/directories` | Листинг каталогов | — |
| GET | `/api/parser-profiles` | Список профилей парсера | `SettingsParser.vue`, `sourceCalls.ts` |
| POST | `/api/parser-profiles` | Создание профиля | `SettingsParser.vue` |
| PUT | `/api/parser-profiles/{profile_id}` | Переименование профиля | `SettingsParser.vue` |
| DELETE | `/api/parser-profiles/{profile_id}` | Удаление профиля с правилами | `SettingsParser.vue` |
| GET | `/api/parser-profiles/{profile_id}/rules` | Правила профиля | `RulesEditor.vue` |
| POST | `/api/parser-profiles/{profile_id}/rules` | Добавление правила | `RulesEditor.vue` |
| PUT | `/api/parser-rules/{rule_id}` | Изменение правила | `RulesEditor.vue` |
| DELETE | `/api/parser-rules/{rule_id}` | Удаление правила | `RulesEditor.vue` |
| POST | `/api/parser-rules/reorder` | Перестановка правил | `RulesEditor.vue` |
| POST | `/api/parser-profiles/test` | Тест профиля на странице или на строках, которые держит окно: строки для конструктора и ответ на каждую (`composition.preview`); живёт в `api_sources.py` | `rulesTestCalls.ts` |
| GET | `/api/logs` | Чтение логов из `logs/*.log` | `LogsModal.vue` |
| GET | `/api/logs/groups` | Список групп логов (distinct по файлам) | `LogsModal.vue` |
| GET | `/api/database/tables` | Список таблиц БД | `DatabaseViewerModal.vue`, `SettingsDebug.vue` |
| GET | `/api/database/table/{table_name}` | Все строки таблицы | `DatabaseViewerModal.vue` |
| POST | `/api/database/clear_table` | Очистка таблицы | `SettingsDebug.vue` |
| PUT | `/api/conform/params` | Как просить демон выравнивания: параметры целиком или коды причин отказа | `ExtensionConform.vue` |
| GET | `/api/rules/line-fields` | Коды полей строки конструктора в его порядке: из них собираются быстрые шаблоны правила «Таблица» | `SettingsParser.vue` |
| GET, POST | `/api/settings/download_limits` | GET: Сколько загрузок идёт одновременно, по способу получения файла; POST: Записать пределы загрузок; значение, которое модуль отвергает, — отказ 400 с причиной | `useDownloadLimits.ts` |
| GET, POST | `/api/settings/source_terms` | GET: Сроки сайтов, откуда источники читают страницы; POST: Записать срок одного сайта; значение, которое отвергает sources, — отказ 400 с причиной | `SourceTerms.vue` |
| GET, POST | `/api/settings/torrent_staging` | GET: Папка, в которую скачиваются раздачи торрент-источников; POST: Записать папку раздач; значение, которое отвергает torrents, — отказ 400 с причиной | `SettingsSources.vue` |
| GET | `/api/voices/players` | Каждый плеер порядка источников и его место среди источников сериалов | `SettingsVoices.vue` |

### 3.6 Служебные — `module.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/stream` | SSE-поток событий шины (`frames=each\ | `useSSE.ts` |
| GET | `/` | Боевой фронт (`web/dist/index.html`) | браузер |
| GET | `/legacy` | Старый фронт (откат) | браузер |
| GET | `/v2`, `/v2/` | Редирект 307 на `/` | браузер |
| GET | `/favicon.svg` | Фавикон (при собранном `web/dist`) | браузер |
| POST | `/api/_diag/echo` | Диагностический прокси к шине (только при `diag=True`) | — |

### 3.7 Источники — `api_sources.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series/{series_id}/sources` | Источники сериала по порядку (`catalog.sources.list`) | `sourceCalls.ts` |
| POST | `/api/series/{series_id}/sources` | Добавить источник; ответ `{id}` (`catalog.sources.add`) | `sourceCalls.ts` |
| PUT | `/api/series/{series_id}/sources/{source_id}` | Изменить поля источника: адрес, профиль, режим поиска VK, сезон Vibix, отключённые плееры, место (`catalog.sources.update`) | `sourceCalls.ts` |
| DELETE | `/api/series/{series_id}/sources/{source_id}` | Удалить источник; единственный источник — отказ `last_source` (`catalog.sources.remove`) | `sourceCalls.ts` |
| POST | `/api/sources/preview` | Что показать окну об адресе, который ещё не источник сериала: состояние таблицы, сводка, строки для конструктора (`composition.preview`) | `sourceCalls.ts` |
| POST | `/api/sources/read` | Прочитать страницу сейчас, не дожидаясь срока таблицы (команда `sources.table.read`; тело `{url, options?}`) | `sourceCalls.ts` |
| POST | `/api/sources/resolve` | Какой сайт читает адрес и его канонический вид, без сети (`sources.site.resolve`) | `sourceCalls.ts` |
| POST | `/api/sources/search` | Поиск названий у поставщика внешних источников (розетка, `contracts/provider_socket.md`): страница названий, как он её отдал, либо причина; без поставщика — отказ `provider_not_connected` (`sources.titles.search`) | `sourceCalls.ts` |
| POST | `/api/sources/cancel` | Остановить идущее чтение страницы | `sourceCalls.ts` |
| POST | `/api/sources/fresh` | Когда прочитаны таблицы страниц и до какого срока они свежи | `sourceCalls.ts` |
| GET | `/api/sources/settings` | Настройки источников: зеркала каждого трекера и домены, страницы которых читает сервис | `SettingsSources.vue` |

### 3.8 Выход — `api_output.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series/{series_id}/composition` | Видео-сериал — раскладка (`composition.get`, query `include_excluded`, `sources_only` — только сводки источников; в ответе `levers` — описания рычагов и `availability` — таблица допустимого по состояниям, по которой окно включает управление) | `outputCalls.ts` |
| GET | `/api/series/{series_id}/output` | Референс каждой серии и отметка (`scan.output.get`) | — |
| GET | `/api/series/{series_id}/output/queues` | Очередь референса и очереди озвучек групп уровня в порядке ядра, со значениями рычагов (`composition.queues.get`); серия без сезона — 422 | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/settings` | Значение рычага на уровне сериала, сезона или серии (`composition.settings.set`); вариант в очереди референса без одиночной играющей строки и вариант в очереди озвучки без играющей строки — отказ `not_allowed` | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/settings/batch` | Несколько рычагов одной записью (`composition.settings.set_many`): галочки очереди, «снять все»; одна ревизия, один пересчёт плана; отказ одного пункта отказывает всем | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/switch` | Отключить или включить серии сезона списком `episodes` (одну или выделенные), а сезон целиком — без `episodes` (`scan.output.switch`) | `outputCalls.ts` |
| POST | `/api/series/{series_id}/output/settings/reset` | Вернуть рычаги уровня в наследование (`composition.settings.reset`) | `outputCalls.ts` |

---
| PUT | `/api/series/{series_id}/output/voice-order` | Порядок озвучек сезона, заданный пользователем: порядок дорожек в контейнерах | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/voice-title` | Название дорожки группы озвучек в контейнерах сериала | `outputCalls.ts` |
| GET | `/api/series/{series_id}/plan` | План видео-сериала: актив с происхождением каждого файла, цель каждой серии, неудачи и ждущие команды «Удалить» | `outputCalls.ts` |
| POST | `/api/series/{series_id}/plan/delete` | «Удалить» файла или дорожки в нём: исполнит следующая синхронизация | `outputCalls.ts` |
| POST | `/api/series/{series_id}/plan/delete/cancel` | Вернуть ждущую команду «Удалить» | `outputCalls.ts` |

### 3.9 Торрент-источник — `api_torrents.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/torrent/check` | Прочитать страницу сейчас: отслеживаемая раздача в другой версии начинает обновление | — |
| POST | `/api/torrent/download` | «Скачать»: раздача уходит в клиент в свою папку | `torrentCalls.ts` |
| POST | `/api/torrent/editor` | Редактор торрента страницы: элементы пяти вкладок, папки, неразмещённое, профили | `torrentCalls.ts` |
| POST | `/api/torrent/editor/assign` | Разместить вручную: файлы — по сезону и серии, дорожки — по папке | `torrentCalls.ts` |
| POST | `/api/torrent/editor/auto` | «Авто»: автоматическая раскладка снова делает удалённые папки и размещает снятые дорожки | `torrentCalls.ts` |
| POST | `/api/torrent/editor/folder` | Создать или изменить папку дорожек | `torrentCalls.ts` |
| POST | `/api/torrent/editor/folder/delete` | Удалить папку дорожек | `torrentCalls.ts` |
| POST | `/api/torrent/editor/remove` | «−»: снять дорожки с их папок | `torrentCalls.ts` |
| POST | `/api/torrent/editor/unassign` | Снять ручное размещение | `torrentCalls.ts` |
| POST | `/api/torrent/page` | Что перечисляет страница трекера сейчас (чтение с трекера) | `torrentCalls.ts` |
| POST | `/api/torrent/releases` | Раздачи, которые держит torrents для страницы: состояние, версия, папка, файлы | `torrentCalls.ts` |
| POST | `/api/torrent/watch` | Включить или выключить автоматическое обновление раздачи | `torrentCalls.ts` |

---

## 4. Функции панели → эндпоинты

> Раздел описывает вызовы панели по реестру `contracts/endpoints.json` (2026-11-21): строки маршрутов, которых в шлюзе больше нет
> (разбор страниц, нарезка, медиа-элементы, предпросмотр нейминга, флаги отладки старой системы) и которые панель не вызывает,
> из таблицы убраны; кто вызывает каждый маршрут, видно в колонке «Панель» раздела 3.

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
| Окно добавления: поиск в TMDB | POST | `/api/tmdb/search` |
| Окно добавления: детали TMDB | GET | `/api/tmdb/details/{tmdb_id}` |
| Окно добавления: сохранить серию | POST | `/api/series` |
| Окно добавления: список профилей | GET | `/api/parser-profiles` |
| Статус-окно: карточка | GET | `/api/series/{series_id}` |
| Статус-окно: свойства — сохранить | POST | `/api/series/{series_id}` |
| Статус-окно: свойства — поиск TMDB | POST | `/api/tmdb/search` |
| Статус-окно: свойства — детали TMDB | GET | `/api/tmdb/details/{tmdb_id}` |
| Статус-окно: свойства — профили | GET | `/api/parser-profiles` |
| Статус-окно: вкладка «Композиция» | GET | `/api/series/{series_id}/composition` |
| Статус-окно: вкладка «Композиция» | GET | `/api/series/{series_id}` |
| Статус-окно: раздел «Выход» — раскладка | GET | `/api/series/{series_id}/composition` |
| Статус-окно: раздел «Выход» — отключить или включить серию, выделенные серии, сезон | PUT | `/api/series/{series_id}/output/switch` |
| Статус-окно: раздел «Выход» — рычаг | PUT | `/api/series/{series_id}/output/settings` |
| Статус-окно: раздел «Выход» — галочки очереди разом | PUT | `/api/series/{series_id}/output/settings/batch` |
| Статус-окно: раздел «Выход» — сбросить рычаги уровня | POST | `/api/series/{series_id}/output/settings/reset` |
| Статус-окно: раздел «Выход» — синхронизировать | POST | `/api/series/{series_id}/scan` |
| Настройки → Авторизация: прочитать | GET | `/api/auth` |
| Настройки → Авторизация: сохранить | POST | `/api/auth` |
| Настройки → Аккаунт: сменить пароль | POST | `/api/auth/password` |
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
| Настройки → Правила: имена файлов серии | GET | `/api/series/{series_id}/source-filenames` |
| Настройки → Отладка: настройки сканера | POST | `/api/scanner/settings` |
| Настройки → Отладка: сканировать всё | POST | `/api/scanner/scan_all` |
| Расширения → Conform: очистка очереди по завершении | GET, POST | `/api/settings/conform_clear_finished` |
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

15 из 115 метод-эндпоинтов под `/api` панель не вызывает (в реестре `contracts/endpoints.json` у них пуст `panel_sites`; список
сверен с `web/src` поиском адреса). «Кто вызывает» — поиск адреса в `legacy_frontend/` и `tests/`.

| Метод | Путь | Кто вызывает | Статус |
|---|---|---|---|
| GET | `/api/agent/queue` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/directories` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/downloads/queue` | `legacy_frontend` | доступен, но только старому фронту |
| POST | `/api/downloads/queue/clear` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/downloads/{task_id}/cancel` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/me` | никто (в панели — подстрока адреса в `main.ts`, не вызов) | нигде не вызывается: ни панелью, ни старым фронтом, ни тестами |
| GET | `/api/pack/queue` | никто | нигде не вызывается: ни панелью, ни старым фронтом, ни тестами |
| GET | `/api/series/active_torrents` | `legacy_frontend` | доступен, но только старому фронту |
| PUT | `/api/series/{series_id}/ignored-episodes` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/series/{series_id}/ignored-seasons` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/series/{series_id}/output` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/series/{series_id}/reprocess` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/settings/conform_clear_finished` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/torrent/check` | никто | нигде не вызывается: ни панелью, ни старым фронтом, ни тестами |
| GET | `/api/trackers` | `legacy_frontend` | доступен, но только старому фронту |

---

## 6. Контракты

> Тексты ниже сверены с моделями шлюза для маршрутов серий и источников (2026-11-21); контракт маршрутов, которых здесь нет (план, очереди и рычаги выхода, торрент-источник, расписание, пределы загрузок и других), — модели `core/contracts`
> и схемы `modules/gateway/schemas.py`; их читает документ OpenAPI (раздел 8).

Тела запросов эндпоинтов этапа 10 и сохранения/создания серии — модели в `schemas.py` с `extra="forbid"`: поле,
которого модель не знает, отклоняется кодом 422 с его именем. Прочие тела разбираются вручную (`await request.json()`).
Формы ответов заданы `response_model` в `schemas.py` (база `extra="allow"` — лишние поля не отрезаются; новые
эндпоинты строятся на моделях `core/contracts`). Поля `ignored_seasons`, `ignored_episodes` передаются **строкой JSON**,
а не объектом.

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
`name_en`, `site`, `save_path`, `sources`, `auto_scan_enabled`,
`is_busy`, `statuses`, `last_scan_time`, `tmdb_info`; непостоянные — `season`,
`quality`, `quality_override`, `resolution_override`, `ignored_seasons`,
`ignored_episodes`, `downloaded_episodes_count` (в списке), `planned_save_path` (в карточке). Адреса, типа источника и профиля у самого
сериала нет: они в `sources` — списке `SeriesSource` (`id`, `series_id`,
`position`, `url`, `source_type`, `parser_profile_id`,
`vibix_season`, `players_off`, `rows_per_line`, `color`). `tmdb_info` — маппинг или `null`;
`downloaded_episodes_count` — целое (0, если модуль недоступен).

**POST `/api/series`** — тело `SeriesCreate`. Обязательные: `name`, `name_en`,
`site`, `save_path`, `sources` (минимум один: `{url, source_type, parser_profile_id?,
vibix_season?, players_off?, rows_per_line?}`, `source_type` — `torrent` | `vk_video` |
`web_video`). Необязательные: `season`, `quality`, `quality_override`,
`resolution_override`, `auto_scan_enabled`, `ignored_seasons`, `ignored_episodes`,
`tmdb_data`. Поле, которого модель не знает (в том числе `url`, `source_type`,
`parser_profile_id` на верхнем уровне), отклоняется (422). `tmdb_data` — объект `{tmdb_id, tmdb_season_number, total_episodes,
poster_path, series_name, year}`. Ответ `{success, series_id}`; 400 — отказ catalog
с кодом (`source_exists`, `profile_not_found`) или относительный путь.

**GET `/api/series/{series_id}`** — объект серии с `planned_save_path` (папка, которую сохранение просило и куда
следующая синхронизация переносит файлы). Ошибки: 404 (не найден), 400, 409, 500.

**POST `/api/series/{series_id}`** — тело `SeriesUpdate`: поля серии (без источников),
все необязательные; меняются только переданные, неизвестное поле — 422. Одно сохранение — одна правка (синхронизация между её частями не
начинается): поля серии — в `catalog.series.update`, `tmdb_data` — в `metadata.map.set`, `save_path` — планом переноса в
`library.relocation.plan` (файлы переезжают при следующей синхронизации). Шлюз не запускает скан; раскладка видео пересчитывается
по событию `series.updated`. Ответ
`{success, message}`.

**DELETE `/api/series/{series_id}`** — ответ `{success}`; каскад по событию `series.deleted`.

**POST `/api/series/{series_id}/state`** — тело `{state: ["viewing"]}` при
открытии статус-окна, `{state: []}` при закрытии. Открытие дополнительно
просит владельца файлов раздач (`torrents.fs.verify`) сверить свои файлы с диском;
вида сериала шлюз не знает. Ответ `{success}`.

**POST `/api/series/{series_id}/toggle_auto_scan`** — тело `{enabled}`
(обязательно, иначе 400).

**POST `/api/series/{series_id}/ignored-seasons`** — тело `{seasons: [...]}`;
сохраняется как JSON-строка. 400 при отсутствии ключа.

**PUT `/api/series/{series_id}/ignored-episodes`** — тело
`{episodes: {"<сезон>": [номера]}}`. Ключ — номер серии сериала, а не файл
раздачи: выбор обязан пережить замену релиза. 400 при отсутствии ключа.

**POST `/api/series/{series_id}/scan`** — синхронный: HTTP ждёт результата. Тело необязательно: `{season, episode?}` — синхронизация
в рамках сезона или серии; query `download=false` — без загрузок. Ответ `{success, changed, sources}` (или `cancelled`, `aborted`: поля, которых нет, не
присылаются). 409 — отказ состояния (сериал занят) или поставщика, не поднятого для страниц серии.

### 6.3 Медиа и файлы

**POST `/api/series/{series_id}/reprocess`** — синхронизация сериала без загрузок (диск меняется только синхронизацией); ответ
`{success, message}`; 409 — сериал занят.

**GET `/api/series/{series_id}/source-filenames`** — список базовых имён файлов раздач
торрент-сериала для теста правил; у видео-сериала пуст (строки для его теста даёт
`POST /api/sources/preview`).

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

**GET|POST `/api/settings/conform_clear_finished`** — тело `{enabled}`; ответ GET `{enabled}`, POST `{success}`.
Ключ в БД: `conform.clear_finished`.

**GET|POST|DELETE `/api/settings/saved_paths`** — GET: `{paths: [{id, path}]}`;
POST: тело `{path}` (пустая строка игнорируется); DELETE: тело `{id}` — id
передаётся в теле, не в query. Ответ — актуальный список `{paths}`.

**GET|POST `/api/settings/debug_flags`** — GET:
`{logging_modules: {<группа>: [{name, description, enabled}]}}`
(флаги дампов страниц `save_html_*`, `save_json_vk_scraper` убраны: их никто не читал); POST: тело `{module,
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

**GET `/api/parser-profiles`** — список `{id, name}`.

**POST `/api/parser-profiles`** — тело `{name}`; ответ **201** `{success, id}`;
400 — имя не указано, 409 — профиль с таким именем уже есть.

**PUT `/api/parser-profiles/{profile_id}`** — тело `{name}`; 400 — новое имя
не указано, 409 — дубль имени либо профиля нет.

**DELETE `/api/parser-profiles/{profile_id}`** — ответ `{success}`; 400 с кодом `profile_in_use` —
профиль назначен источникам сериалов.

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
`POST /api/parser-rules/reorder` массивом id. Приоритета
разрешений пользователя больше нет (Д-36): референс выбирает `scan` по разрешению и порядку плееров.

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
(`calc/rules/season.py`).

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
`POST /api/parser-profiles/test`: страница показывается строками для конструктора с ответом на каждую.

### 6.7 Источники

Шлюз передаёт тело в запрос шины и ответ владельца обратно; видов сайтов и плееров он не знает. Отказ владельца —
код причины (`core/reasons`) в поле `error` строкой JSON; формулировку даёт интерфейс. Статусы: `LookupError`
владельца — 404, `ValueError` — 400, модуля нет на шине — 503.

**GET `/api/series/{series_id}/sources`** — список `SeriesSource`; 404 — сериала нет.

**POST `/api/series/{series_id}/sources`** — тело `{url, source_type, parser_profile_id?, vibix_season?,
players_off?, rows_per_line?, position?}`; поля, которых нет в теле, шлюз не посылает, умолчания подставляет `catalog`. Ответ `{id}`.
400: `source_exists` (тот же адрес у сериала), `profile_not_found`; 404 — сериала нет.

**PUT `/api/series/{series_id}/sources/{source_id}`** — тело: любое из `url`, `parser_profile_id`,
`vibix_season`, `players_off`, `rows_per_line`, `position`; не присланное остаётся, `null` снимает профиль и сезон Vibix; `source_type` и
прочие поля — 422. Ответ `{success, applied}` (`applied` — поля, отличавшиеся от хранимых; без изменений события нет).
404 с кодом `source_not_found` — источник другого сериала или его нет.

**DELETE `/api/series/{series_id}/sources/{source_id}`** — ответ `{success}`; 400 с кодом `last_source` — единственный
источник сериала; 404 с `source_not_found`.

**POST `/api/sources/preview`** — тело `CompositionPreviewRequest` `{url, options?, profile_id?, series_season?,
refresh?}`; ответ `CompositionPreviewReply` `{url, state (ready | reading | failed | absent), reason?, summary?,
lines: [{key, line, result?}]}`. `refresh: false` ничего не читает. Пока страница читается, `state: reading`, итог —
события `sources_table_ready` / `sources_table_failed` (запрос повторяется без чтения). Ничего не пишет.

**POST `/api/sources/resolve`** — тело `{url, options?}`; ответ `{site, canonical_url}`, оба `null`, если адрес не читает
ни один сайт; сети нет.

**POST `/api/parser-profiles/test`** — тело `{url, options?, profile_id, series_season?, refresh?, lines?}` (`profile_id` обязателен,
нужен и адрес, или `lines`, иначе 422); ответ — тот же `CompositionPreviewReply`, у каждой строки `result` — ответ
конструктора (`excluded`, `season`, `episode`, `start`, `end`, `part`, `voiceover`, `events`, `errors`). С `lines`
(названия, набранные вручную, или имена файлов раздачи) страница не читается, ответ — `ready`, ключ строки — её место в
списке.

### 6.8 Выход

**GET `/api/series/{series_id}/composition`** — видео-сериал: `CompositionReply` `{series_id, rev, rows, sources, levels,
settings}` (query `include_excluded` — вернуть и строки, которые конструктор исключил или оставил без номера серии;
`refresh` игнорируется: синхронизацию запускает `POST /api/series/{series_id}/scan`); торрент-сериал: список состава
раздач, query `refresh` сначала выполняет `scan.series.prepare`. 404 — сериал не найден.

**GET `/api/series/{series_id}/output`** — список `Reference` `{season, episode, part, mark, source_id, row_key, player,
voice_group, height, reason}`; `mark` — `by_level` | `own` | `other_player` | `none` | `unfit` | `off` (серию отключил пользователь:
сезон, номер серии или строка плана; референса и озвучек у неё нет). Сведения раскладки и
озвучек (`/composition`, `/voices`) шлюз не склеивает: интерфейс сопоставляет их по (сезон, серия).

**PUT `/api/series/{series_id}/output/switch`** — тело `{season, episodes?, off}`; `episodes` — список серий сезона
(одна или выделенные, одной командой), без `episodes` — сезон целиком. Память сериала (`ignored_seasons`,
`ignored_episodes`) и строки плана меняются вместе, одним изменением; включение сезона включает в нём всё. Ответ
`{success}`; изменение приходит событиями `reference_changed`. 404 — сериал не найден; 422 — нет `season` или `off`,
пустой список `episodes` либо неизвестное поле.

**PUT `/api/series/{series_id}/output/settings`** — тело `{season?, episode?, lever, target?, value}`; уровень — сериал
(без сезона), сезон или серия (сезон и серия). Ответ `{success}`. 400 с кодом: `unknown_lever`, `bad_target`,
`bad_value`; 422 — серия без сезона или неизвестное поле тела.

**PUT `/api/series/{series_id}/output/settings/batch`** — тело `{items: [{season?, episode?, lever, target?, value}, …]}`,
не меньше одного пункта; каждый пункт — тело одиночной записи, серия берётся из пути. Пункты применяются по порядку одной
записью: одна ревизия раскладки, одно событие, один пересчёт плана на все. Отказ одного пункта (коды те же, что у
одиночной записи) отказывает всем, ничего не пишется. Ответ `{success}`; 422 — пустой список, пункт без сезона у серии
или неизвестное поле.

**POST `/api/series/{series_id}/output/settings/reset`** — тело `{season?, episode?, lever?, target?}`; без `lever`
возвращаются все рычаги уровня. Ответ `{success}`; отказы — как у записи.

---

## 7. Поток событий SSE

Один эндпоинт — `GET /api/stream`, `text/event-stream`. Кадр при
`frames=each` (по умолчанию): `event: <имя>\ndata: <json>\n\n`; при
`frames=batch` (боевой фронт) — кадр на ход: `event: batch\ndata: [{event, data}, ...]\n\n`
(подробно — [sse_contract.md](sse_contract.md)); каждые 15 с — keepalive-комментарий. Топики
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
| `scanner_status_update` | `scan.status.changed` | статус сканера |
| `series_sources_changed` | `series.source.added` | `{series_id, source_id, what}` (`SourceChanged`) |
| `series_sources_changed` | `series.source.updated` | `{series_id, source_id, what}` (`SourceChanged`) |
| `series_sources_changed` | `series.source.removed` | `{series_id, source_id, what}` (`SourceChanged`) |
| `sources_table_progress` | `sources.table.progress` | `{url, phase, current, total}` |
| `sources_table_ready` | `sources.table.ready` | `{url, read_at}` |
| `sources_table_fresh` | `sources.table.fresh` | `{url, read_at, fresh_until}`: срок таблицы страницы сдвинулся (`TableReady`) |
| `torrent_release_update` | `torrents.release.changed` | раздача торрент-источника как она есть (`ReleaseView`) |
| `torrent_editor_update` | `unpack.editor.changed` | `{url}` (`PageChanged`) |
| `torrent_read_update` | `unpack.read.progress` | `{url, release, done, total}` (`ReleaseReadProgress`) |
| `sources_table_failed` | `sources.table.failed` | `{url, reason}` |
| `composition_changed` | `composition.changed` | `{series_id, rev, episodes, summary_only}` |
| `composition_failed` | `composition.failed` | `{series_id, reason}` |
| `series_read_progress` | `composition.series.reading` | как далеко прочитана страница источника сериала (`SeriesReadProgress`) |
| `conform_queue_update` | `conform.queue.changed` | картина очереди выравнивания демона |
| `pack_queue_update` | `planner.pack.changed` | массив карточек упаковки (`PackCard`) |
| `pack_journal_update` | `planner.journal.changed` | массив журналов применения (`PackJournal`) |
| `plan_update` | `planner.series.changed` | вид плана целиком (`PlannerView`) |
| `voice_settings_update` | `voices.settings.changed` | настройки озвучек (`VoiceSettingsView`) |
| `sources_settings_update` | `sources.settings.changed` | настройки источников (`SourcesSettingsView`) |
| `torrent_staging_update` | `torrents.staging.changed` | папка раздач (`StagingFolder`) |
| `conform_settings_update` | `conform.settings.changed` | настройки выравнивания (`ConformSettingsView`) |
| `provider_update` | `sources.provider.changed` | картина поставщика внешних источников (`ProviderView`) |
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

Типы фронта лежат в `web/src/api/schema.d.ts`; они пересоздаются из схемы живого приложения. Команда `npm run gen:api` берёт схему у экземпляра
на `:5000`; стенд на свободном порту поднимается `uvicorn run:app` с `ST_DOCS_ENABLED=true` на новой БД.

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
`PUT /api/series/{series_id}/ignored-episodes`, `GET /api/settings/
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
