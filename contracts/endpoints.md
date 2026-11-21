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
`module.py` (6: служебные пути, `/api/stream` и диагностический).

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
эндпоинт не вызывается (раздел 5). Описание — из реестра (`SUMMARIES` в `scripts/gen_endpoints.py`).

### 3.1 Авторизация — `api_auth.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/auth/password` | Смена пароля администратором | `SettingsAccount.vue` |
| GET | `/api/auth/status` | Статус онбординга: настроен ли админ, вошёл ли клиент | `useRealtime.ts`, `main.ts` |
| POST | `/api/login` | Вход администратора | `LoginModal.vue` |
| POST | `/api/logout` | Выход (очистка сессии) | `App.vue` |
| GET | `/api/me` | Кто сейчас вошёл | — |
| POST | `/api/setup` | Первичное создание администратора | `SetupModal.vue` |

### 3.2 Серии — `api_series.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET, POST | `/api/series` | GET: Список сериалов; POST: Создание серии | `series.ts`, `AddSeriesModal.vue` |
| GET | `/api/series/active_torrents` | Прогресс активных загрузок | — |
| GET, POST, DELETE | `/api/series/{series_id}` | GET: Карточка серии; POST: Сохранение свойств серии; DELETE: Удаление серии | `StatusModal.vue`, `StatusProperties.vue`, `App.vue` |
| POST | `/api/series/{series_id}/failures/reset` | Забыть неудачные попытки сериала: все или одного уровня (сезон, серия, озвучка, нарезки) | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/ignored-episodes` | Память отключённых серий | — |
| POST | `/api/series/{series_id}/ignored-seasons` | Список игнорируемых сезонов | — |
| POST | `/api/series/{series_id}/scan` | Синхронный скан одной серии | `App.vue`, `outputCalls.ts` |
| POST | `/api/series/{series_id}/scan/cancel` | Остановить синхронизацию сериала: чтение страниц, загрузки, задачи выравнивания, сборку файлов | `App.vue`, `outputCalls.ts` |
| POST | `/api/series/{series_id}/state` | Открытие/закрытие статус-окна (viewing) | `ui.ts` |
| POST | `/api/series/{series_id}/toggle_auto_scan` | Включение/выключение автоскана | `App.vue` |

### 3.3 Медиа и файлы — `api_media.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/series/{series_id}/reprocess` | Переобработка имён файлов: синхронизация сериала без загрузок (scan) | — |
| GET | `/api/series/{series_id}/source-filenames` | Имена файлов раздач сериала для теста правил торрента | `rulesTestCalls.ts` |

### 3.4 Скан и очереди — `api_system.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/agent/queue` | Очередь торрент-конвейера | — |
| DELETE | `/api/conform/jobs/{job_id}` | Остановить задачу выравнивания, которая ждёт или идёт, либо удалить запись завершённой | `AgentsPanel.vue` |
| GET | `/api/conform/jobs/{job_id}/plot/{name}` | Картинка графика выравнивания озвучки (PNG), как её сделал демон | `work.ts` |
| DELETE | `/api/downloads/cards/{card_id}` | Убрать карточку загрузки, оставшуюся после синхронизации | `AgentsPanel.vue` |
| GET | `/api/downloads/queue` | Очередь загрузок видео | — |
| POST | `/api/downloads/queue/clear` | Очистка очереди загрузок | — |
| POST | `/api/downloads/{task_id}/cancel` | Отмена загрузки: процесс, файл, задача | — |
| DELETE | `/api/pack/cards/{card_id}` | Убрать карточку упаковки, оставшуюся после синхронизации | `AgentsPanel.vue` |
| GET | `/api/pack/queue` | Очередь упаковки: карточка на контейнер, который собирает синхронизация, его стадии и дорожки | — |
| POST | `/api/scanner/scan_all` | Полный проход по всем сериалам | `ScheduleSettings.vue` |
| POST | `/api/scanner/settings` | Настройки планировщика (вкл/выкл, интервал) | `ScheduleSettings.vue` |
| GET | `/api/scanner/status` | Состояние планировщика сканирования | `scanner.ts` |
| POST | `/api/schedule` | Времена суток синхронизаций и проверок торрент-раздач; расписание, которое scan отвергает, — отказ 400 с причиной | `ScheduleSettings.vue` |

### 3.5 Настройки, справочники, логи, БД — `api_settings.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET, POST | `/api/auth` | GET: Креды внешних сервисов без секретов; POST: Сохранение кредов сервисов | `SettingsAuth.vue` |
| GET | `/api/auth/{service}/secret` | Секрет одного сервиса по просьбе глаза его поля (не кэшируется); пустой, если не задан | `SettingsAuth.vue` |
| PUT | `/api/conform/params` | Как просить демон выравнивания: параметры целиком или коды причин отказа | `ExtensionConform.vue` |
| GET, PUT | `/api/conform/settings` | GET: Адрес демона выравнивания, рабочая папка здесь и на демоне, очистка очереди по завершении (только чтение): как действуют сейчас; PUT: Записать адрес и рабочую папку целиком (адрес применяется сразу) или получить коды причин отказа | `ExtensionConform.vue` |
| POST | `/api/database/clear_table` | Очистка таблицы БД | `SettingsDebug.vue` |
| GET | `/api/database/table/{table_name}` | Все строки таблицы БД | `DatabaseViewerModal.vue` |
| GET | `/api/database/tables` | Список таблиц БД | `DatabaseViewerModal.vue`, `SettingsDebug.vue` |
| GET | `/api/directories` | Листинг каталогов | — |
| GET | `/api/logs` | Чтение логов из logs/*.log | `LogsModal.vue` |
| GET | `/api/logs/groups` | Список групп логов | `LogsModal.vue` |
| GET, POST | `/api/parser-profiles` | GET: Список профилей парсера; POST: Создание профиля парсера | `SettingsParser.vue`, `sourceCalls.ts` |
| PUT, DELETE | `/api/parser-profiles/{profile_id}` | PUT: Переименование профиля парсера; DELETE: Удаление профиля парсера с правилами | `SettingsParser.vue` |
| GET, POST | `/api/parser-profiles/{profile_id}/rules` | GET: Правила профиля; POST: Добавление правила в профиль | `RulesEditor.vue` |
| POST | `/api/parser-rules/reorder` | Перестановка правил | `RulesEditor.vue` |
| PUT, DELETE | `/api/parser-rules/{rule_id}` | PUT: Изменение правила; DELETE: Удаление правила | `RulesEditor.vue` |
| GET, PUT | `/api/provider` | GET: Поставщик внешних источников (Chines): адрес, состояние розетки, манифест, приложения с числом источников и линий, папка загрузок (токен — только признак наличия); PUT: Записать адрес и токен поставщика (адрес применяется сразу, токен не задан — прежний остаётся, пустой — удаляется) или получить коды причин отказа | `ExtensionChines.vue`, `provider.ts` |
| GET | `/api/rules/line-fields` | Коды полей строки конструктора в его порядке: из них собираются быстрые шаблоны правила «Таблица» | `SettingsParser.vue` |
| GET, POST | `/api/settings/conform_clear_finished` | GET: Флаг очистки очереди выравнивания по завершении; POST: Записать флаг очистки очереди выравнивания по завершении | `ExtensionConform.vue` |
| GET, POST | `/api/settings/debug_flags` | GET: Структура логирования с флагами отладки; POST: Включение/выключение отладочного логирования | `LogsLoggingTab.vue` |
| GET, POST | `/api/settings/download_limits` | GET: Сколько загрузок идёт одновременно, по способу получения файла; POST: Записать пределы загрузок; значение, которое модуль отвергает, — отказ 400 с причиной | `useDownloadLimits.ts` |
| GET, POST, DELETE | `/api/settings/saved_paths` | GET: Список сохранённых путей загрузки; POST: Добавление сохранённого пути; DELETE: Удаление сохранённого пути | `SavedPathDropdown.vue`, `SettingsDebug.vue` |
| GET, POST | `/api/settings/source_terms` | GET: Сроки сайтов, откуда источники читают страницы; POST: Записать срок одного сайта; значение, которое отвергает sources, — отказ 400 с причиной | `SourceTerms.vue` |
| GET, POST | `/api/settings/torrent_staging` | GET: Папка, в которую скачиваются раздачи торрент-источников; POST: Записать папку раздач; значение, которое отвергает torrents, — отказ 400 с причиной | `SettingsSources.vue` |
| GET | `/api/tmdb/details/{tmdb_id}` | Детали сериала TMDB | `StatusProperties.vue`, `useTmdbChoice.ts` |
| POST | `/api/tmdb/search` | Поиск сериала в TMDB | `useTmdbChoice.ts` |
| GET | `/api/trackers` | Список трекеров с зеркалами | — |
| PUT | `/api/trackers/{tracker_id}` | Обновление зеркал трекера | `SettingsSources.vue` |
| GET | `/api/voices/players` | Каждый плеер порядка источников и его место среди источников сериалов | `SettingsVoices.vue` |
| GET, PUT | `/api/voices/settings` | GET: Группы озвучек и порядок источников: как действуют, порядок по умолчанию; PUT: Записать группы и порядок источников целиком или получить коды причин отказа | `SettingsVoices.vue` |

### 3.6 Служебные — `module.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/` | Боевой фронт панели (web/dist/index.html) | браузер |
| POST | `/api/_diag/echo` | Диагностический прокси к шине (только при diag=True) | — |
| GET | `/api/stream` | SSE-поток событий шины | `useSSE.ts` |
| GET | `/legacy` | Старый фронт (откат), ассеты по /static | браузер |
| GET | `/v2` | Редирект 307 на корень | браузер |
| GET | `/v2/` | Редирект 307 на корень | браузер |

### 3.7 Источники — `api_sources.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| POST | `/api/parser-profiles/test` | Тест профиля правил на странице: строки для конструктора и ответ на каждую (composition.preview) | `rulesTestCalls.ts` |
| GET, POST | `/api/series/{series_id}/sources` | GET: Источники сериала по порядку; POST: Добавление источника сериалу (ответ {id}) | `sourceCalls.ts` |
| PUT, DELETE | `/api/series/{series_id}/sources/{source_id}` | PUT: Изменение полей источника: адрес, профиль, режим поиска, сезон Vibix, отключённые плееры, место; DELETE: Удаление источника (единственный не удаляется: last_source) | `sourceCalls.ts` |
| POST | `/api/sources/cancel` | Остановить идущее чтение страницы | `sourceCalls.ts` |
| POST | `/api/sources/fresh` | Когда прочитаны таблицы страниц и до какого срока они свежи | `sourceCalls.ts` |
| POST | `/api/sources/preview` | Что показать окну об адресе, который ещё не источник: состояние таблицы, сводка, строки для конструктора | `sourceCalls.ts` |
| POST | `/api/sources/read` | Прочитать страницу сейчас, не дожидаясь срока её таблицы | `sourceCalls.ts` |
| POST | `/api/sources/resolve` | Какой сайт читает адрес и его канонический вид (без сети) | `sourceCalls.ts` |
| POST | `/api/sources/search` | Поиск названий у поставщика внешних источников: страница названий как он её даёт | `sourceCalls.ts` |
| GET | `/api/sources/settings` | Настройки источников: зеркала каждого трекера и домены, страницы которых читает сервис | `SettingsSources.vue` |

### 3.8 Выход — `api_output.py`

| Метод | Путь | Назначение | Панель |
|---|---|---|---|
| GET | `/api/series/{series_id}/composition` | Раскладка видео-сериала (composition) | `outputCalls.ts` |
| GET | `/api/series/{series_id}/output` | Референс каждой серии и отметка (planner) | — |
| GET | `/api/series/{series_id}/output/queues` | Очереди референса и озвучек групп уровня в порядке ядра, со значениями рычагов | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/settings` | Значение рычага выхода на уровне сериала, сезона или серии | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/settings/batch` | Несколько рычагов одной записью: галочки очереди, «снять все» | `outputCalls.ts` |
| POST | `/api/series/{series_id}/output/settings/reset` | Возврат рычагов уровня в наследование | `outputCalls.ts` |
| PUT | `/api/series/{series_id}/output/switch` | Отключить или включить серии сезона списком, а сезон целиком — без списка | `outputCalls.ts` |
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

> Экраны панели и маршруты, которые они зовут (основные вызовы; полный перечень — колонка «Панель» раздела 3). Строку с маршрутом,
> которого нет в шлюзе или которого панель не вызывает, генератор `scripts/gen_endpoints.py` не пропускает.

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
| Расширения → Conform: очистка очереди по завершении | POST | `/api/settings/conform_clear_finished` |
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

15 из 115 метод-эндпоинтов под `/api` панель не вызывает (в реестре `contracts/endpoints.json` у них пуст `panel_sites`).
«Кто вызывает» — поиск адреса в `legacy_frontend/`, `tests/` и `tools/`.

| Метод | Путь | Кто вызывает | Статус |
|---|---|---|---|
| GET | `/api/agent/queue` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/directories` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/downloads/queue` | `legacy_frontend` | доступен, но только старому фронту |
| POST | `/api/downloads/queue/clear` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/downloads/{task_id}/cancel` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/me` | только прибор `tools/checks/window_json.py` | только прибор сверки окна; в панели, у старого фронта и в тестах вызова нет |
| GET | `/api/pack/queue` | только прибор `tools/checks/window_json.py` | только прибор сверки окна; в панели, у старого фронта и в тестах вызова нет |
| GET | `/api/series/active_torrents` | `legacy_frontend` | доступен, но только старому фронту |
| PUT | `/api/series/{series_id}/ignored-episodes` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/series/{series_id}/ignored-seasons` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/series/{series_id}/output` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/series/{series_id}/reprocess` | `legacy_frontend` | доступен, но только старому фронту |
| GET | `/api/settings/conform_clear_finished` | только тесты | только тесты; в панели нет вызова |
| POST | `/api/torrent/check` | только прибор `tools/checks/window_json.py` | только прибор сверки окна; в панели, у старого фронта и в тестах вызова нет |
| GET | `/api/trackers` | `legacy_frontend` | доступен, но только старому фронту |

---

## 6. Контракты

Разделы 6.1–6.9 сняты с документа OpenAPI шлюза (модели тел и ответов) и со списка `MANUAL` в `scripts/endpoints_md.py` для того, что
обработчик читает сам (`await request.json()`) или отдаёт словарём любой формы. Тело, у которого модель с `extra="forbid"`, отклоняет
поле, которого модель не знает, кодом 422 с его именем. 422 есть у каждого маршрута с телом или параметрами (`HTTPValidationError`).
Описание маршрута — его докстринг (источник — код). Вложенные модели показаны именем; их поля — в документе OpenAPI (`/openapi.json`,
`ST_DOCS_ENABLED=true`) и в `core/contracts`.

### 6.1 Авторизация

**POST `/api/auth/password`** — Смена пароля администратором.

> The change of the password by the administrator who is signed in (the section «Аккаунт» of the settings).
> Behind the «lock» (a session is needed): checks the length of the new password (≥8) and renews the hash. The name does not change — the
> session stays valid.
>
> The current password is NOT asked: the active session already confirms the owner (a deliberate decision for a single-user application —
> not to make him recall the old password when changing it from under a login).

- Тело (читает сам обработчик): `{new}` (не короче 8 знаков)
- Ответ `200`: `{success}`; 400 — пароль короток, 401 — нет сессии

**GET `/api/auth/status`** — Статус онбординга: настроен ли админ, вошёл ли клиент.

> The public status for the onboarding: whether the administrator is set up, his name and whether the current client is signed in.
> By it the front chooses the setup or login modal and shows the name of the administrator (admin) on the login screen in place of the
> field for the login (the application is single-user).

- Ответ `200`: `{configured, authenticated, username, admin}`

**POST `/api/login`** — Вход администратора.

- Тело (читает сам обработчик): `{username, password}`
- Ответ `200`: `{success, username}`; 401 — неверная пара, 429 — адрес забанен, 503 — модуль `auth` не отвечает

**POST `/api/logout`** — Выход (очистка сессии).

- Ответ `200`: `{success}`

**GET `/api/me`** — Кто сейчас вошёл.

- Ответ `200`: `{authenticated, username}`; 401 без сессии

**POST `/api/setup`** — Первичное создание администратора.

> The first setup of the administrator. Available ONLY while there is no administrator; after it is made — 403 (it cannot
> be made again bypassing the login). Success signs in at once (the session).

- Тело (читает сам обработчик): `{username, password}` (пароль не короче 8 знаков)
- Ответ `200`: `{success, username}`; 400 — имя пусто или пароль короток, 403 — администратор уже есть

### 6.2 Серии

**GET `/api/series`** — Список сериалов.

- Ответ `200`: `SeriesObject[]` (список)
  - `id` — integer, обязательное
  - `name` — string, обязательное
  - `name_en` — string, обязательное
  - `site` — string, обязательное
  - `save_path` — string, обязательное
  - `season` — string | null, необязательное
  - `sources` — SeriesSource[], обязательное
  - `auto_scan_enabled` — boolean, обязательное
  - `is_busy` — boolean, обязательное
  - `statuses` — string[], обязательное
  - `quality` — string | null, необязательное
  - `quality_override` — string | null, необязательное
  - `resolution_override` — string | null, необязательное
  - `ignored_seasons` — string | null, необязательное
  - `ignored_episodes` — string | null, необязательное
  - `last_scan_time` — string | null, необязательное
  - `tmdb_info` — TmdbInfo | null, необязательное
  - `planned_save_path` — string | null, необязательное

**POST `/api/series`** — Создание серии.

- Тело: `SeriesCreate` (лишнее поле отклоняется — 422)
  - `name` — string, обязательное
  - `name_en` — string, обязательное
  - `site` — string, обязательное
  - `save_path` — string, обязательное
  - `season` — string | null, необязательное
  - `quality` — string | null, необязательное
  - `quality_override` — string | null, необязательное
  - `resolution_override` — string | null, необязательное
  - `auto_scan_enabled` — boolean | null, необязательное
  - `ignored_seasons` — string | null, необязательное
  - `ignored_episodes` — string | null, необязательное
  - `tmdb_data` — object | null, необязательное
  - `sources` — SourceDraft[], обязательное
- Ответ `200`: `CreatedSeries`
  - `success` — boolean, обязательное
  - `series_id` — integer, обязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**GET `/api/series/active_torrents`** — Прогресс активных загрузок.

> The progress of the torrent downloads with the names of the series (the table «Мониторинг» of the agents tab;
> Р-23 — the fix of the mapping of block 2: the source is torrent_progress of the module torrents, not the queue of the pipeline).

- Ответ `200`: `QueueTask[]` (список)
- Форма ответа: список задач монитора (`tasks`)

**GET `/api/series/{series_id}`** — Карточка серии.

- Ответ `200`: `SeriesObject`
  - `id` — integer, обязательное
  - `name` — string, обязательное
  - `name_en` — string, обязательное
  - `site` — string, обязательное
  - `save_path` — string, обязательное
  - `season` — string | null, необязательное
  - `sources` — SeriesSource[], обязательное
  - `auto_scan_enabled` — boolean, обязательное
  - `is_busy` — boolean, обязательное
  - `statuses` — string[], обязательное
  - `quality` — string | null, необязательное
  - `quality_override` — string | null, необязательное
  - `resolution_override` — string | null, необязательное
  - `ignored_seasons` — string | null, необязательное
  - `ignored_episodes` — string | null, необязательное
  - `last_scan_time` — string | null, необязательное
  - `tmdb_info` — TmdbInfo | null, необязательное
  - `planned_save_path` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}`** — Сохранение свойств серии.

> Saving the properties: catalog, then metadata, then the plan of library for the folder.
> The sources are not changed here; the layout of a video series is counted again on
> series.updated. The files take the new names and folder at the next sync (И-59).

- Тело: `SeriesUpdate` (лишнее поле отклоняется — 422)
  - `name` — string | null, необязательное
  - `name_en` — string | null, необязательное
  - `site` — string | null, необязательное
  - `save_path` — string | null, необязательное
  - `season` — string | null, необязательное
  - `quality` — string | null, необязательное
  - `quality_override` — string | null, необязательное
  - `resolution_override` — string | null, необязательное
  - `auto_scan_enabled` — boolean | null, необязательное
  - `ignored_seasons` — string | null, необязательное
  - `ignored_episodes` — string | null, необязательное
  - `tmdb_data` — object | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**DELETE `/api/series/{series_id}`** — Удаление серии.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/failures/reset`** — Забыть неудачные попытки сериала: все или одного уровня (сезон, серия, озвучка, нарезки).

> The failed attempts of the series are forgotten: the rejected and failed voice-overs begin with the first source of
> their chain, a video that did not download takes part in the choice of the reference again. This is an edit of the series, not a sync:
> the work on them goes with the next sync. With a body — only the attempts of one level (a season, an episode, a voice-over, the cuts):
> the attempts of the rest stay, and the choice of the reference by them does not change.

- Тело (необязательно): `FailuresResetBody` (лишнее поле отклоняется — 422)
  - `season` — integer | null, необязательное
  - `episode` — integer | null, необязательное
  - `voice` — string | null, необязательное
  - `kind` — "download" | "align" | "cut" | null, необязательное
- Ответ `200`: `FailuresReset`
  - `success` — boolean, обязательное
  - `reset` — integer, необязательное, по умолчанию `0`
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/ignored-episodes`** — Память отключённых серий.

> The memory of the switched-off episodes: {"episodes": {"<season>": [numbers]}}.
>
> The key is the number of the episode of the series, not the file of a release: the choice must survive the replacement of a release.

- Тело (читает сам обработчик): `{episodes: {"<сезон>": [номера]}}` (обязательно, иначе 400); в карточке хранится строкой JSON
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/ignored-seasons`** — Список игнорируемых сезонов.

- Тело (читает сам обработчик): `{seasons: [...]}` (обязательно, иначе 400); в карточке хранится строкой JSON
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/scan`** — Синхронный скан одной серии.

> A synchronous scan of one series, as in the original: HTTP waits for the result.
> download=false — a scan without a download: the check of the files, the tables, the layout and the plan, but nothing is fetched.
> With a body (a season, an episode of the season) — a sync within the season or the episode: the sources are the same, what is carried out
> is what concerns the scope (an episode of a compilation brings the whole compilation); the series is busy for all of it.

- Query `download`: boolean, необязательный, по умолчанию `true`
- Тело (необязательно): `SyncScopeBody` (лишнее поле отклоняется — 422)
  - `season` — integer, обязательное
  - `episode` — integer | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `409, 500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/scan/cancel`** — Остановить синхронизацию сериала: чтение страниц, загрузки, задачи выравнивания, сборку файлов.

> Stops the sync of the series: the reading of the pages, the downloads, the jobs of the alignment service, the build of
> the files. Nothing is applied from a stopped sync; it waits for a journal of the library under way.

- Ответ `200`: `SyncCancelled`
  - `success` — boolean, обязательное
  - `cancelled` — boolean, необязательное, по умолчанию `false`
- Ответ `500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/state`** — Открытие/закрытие статус-окна (viewing).

> The old contract of the front: ['viewing'] when the modal opens, [] when it closes → the ephemeral viewing (Р-11).
> Opening the modal also starts the check of the files against the disk (Р-13/Р-14: the replacement of the periodic background checks
> of the old system).

- Тело (читает сам обработчик): `{state: ["viewing"]}` при открытии окна, `{state: []}` при закрытии
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное

**POST `/api/series/{series_id}/toggle_auto_scan`** — Включение/выключение автоскана.

- Тело (читает сам обработчик): `{enabled}` (обязательно, иначе 400)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

### 6.3 Медиа и файлы

**POST `/api/series/{series_id}/reprocess`** — Переобработка имён файлов: синхронизация сериала без загрузок (scan).

> The files take the names of the rules: the disk changes in a sync alone (И-59), so this is a sync of the
> series that fetches nothing; refused while one runs.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**GET `/api/series/{series_id}/source-filenames`** — Имена файлов раздач сериала для теста правил торрента.

> File names of the series' torrents for the rules test; a video series has none, its lines
> for the test come from composition.preview.

- Ответ `200`: `string[]` (список)

### 6.4 Скан и очереди

**GET `/api/agent/queue`** — Очередь торрент-конвейера.

- Ответ `200`: `QueueTask[]` (список)
- Форма ответа: список задач очереди торрент-конвейера

**DELETE `/api/conform/jobs/{job_id}`** — Остановить задачу выравнивания, которая ждёт или идёт, либо удалить запись завершённой.

> Stops a job that waits or runs; deletes the record of a job that ended.

- Ответ `200`: `ConformJobChange`
  - `done` — boolean, обязательное
- Ответ `502`: `ErrorOnly` — {`error`}

**GET `/api/conform/jobs/{job_id}/plot/{name}`** — Картинка графика выравнивания озвучки (PNG), как её сделал демон.

> The picture of an alignment plot, as the daemon made it.

- Ответ `200`: `картина PNG`
- Ответ `404, 502`: `ErrorOnly` — {`error`}

**DELETE `/api/downloads/cards/{card_id}`** — Убрать карточку загрузки, оставшуюся после синхронизации.

> A card that stayed after its sync is removed.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `404, 500`: `ErrorResponse` — {`success`, `error`}

**GET `/api/downloads/queue`** — Очередь загрузок видео.

> The cards of the downloads (core/contracts/downloads.py): one to a need of a plan, its stages and attempts.

- Ответ `200`: `DownloadCard[]` (список)
  - `id` — string, обязательное
  - `need` — DownloadNeed, обязательное
  - `variant` — DownloadVariant | null, необязательное
  - `status` — "queued" | "link" | "download" | "remux" | "check" | "done" | "failed", необязательное, по умолчанию `"queued"`
  - `method` — "" | "ranged" | "ffmpeg" | "hls" | "ytdlp_hls" | "ytdlp_dash", необязательное, по умолчанию `""`
  - `stages` — StageView[], необязательное
  - `log` — AttemptLine[], необязательное
  - `figures` — Figures, необязательное
  - `replanned` — boolean, необязательное, по умолчанию `false`
  - `position` — integer | null, необязательное
  - `running` — integer, необязательное, по умолчанию `0`
  - `limit` — integer, необязательное, по умолчанию `0`
  - `queued_at` — string, необязательное, по умолчанию `""`
  - `finished_at` — string, необязательное, по умолчанию `""`
  - `seconds` — number, необязательное, по умолчанию `0.0`

**POST `/api/downloads/queue/clear`** — Очистка очереди загрузок.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `500`: `ErrorResponse` — {`success`, `error`}

**POST `/api/downloads/{task_id}/cancel`** — Отмена загрузки: процесс, файл, задача.

> A pinpoint cancel of a download: kill the process, remove the file, remove the task (finding 45).

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `404, 500`: `ErrorResponse` — {`success`, `error`}

**DELETE `/api/pack/cards/{card_id}`** — Убрать карточку упаковки, оставшуюся после синхронизации.

> A card of the packing that stayed after its sync is removed.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `404, 500`: `ErrorResponse` — {`success`, `error`}

**GET `/api/pack/queue`** — Очередь упаковки: карточка на контейнер, который собирает синхронизация, его стадии и дорожки.

> The cards of the packing (core/contracts/packing.py): every container a sync builds, its stages and tracks.

- Ответ `200`: `PackCard[]` (список)
  - `id` — string, обязательное
  - `series_id` — integer, обязательное
  - `series_name` — string, необязательное, по умолчанию `""`
  - `sync_started` — string, необязательное, по умолчанию `""`
  - `episode` — string, необязательное, по умолчанию `""`
  - `name` — string, необязательное, по умолчанию `""`
  - `video` — DownloadVariant | null, необязательное
  - `status` — "queued" | "build" | "verify" | "built" | "apply" | "done" | "failed", необязательное, по умолчанию `"queued"`
  - `stages` — PackStageView[], необязательное
  - `tracks` — PackTrack[], необязательное
  - `reason` — "" | "order" | "video" | "names", необязательное, по умолчанию `""`
  - `waited` — boolean, необязательное, по умолчанию `false`
  - `failure` — string, необязательное, по умолчанию `""`
  - `figures` — PackFigures, необязательное
  - `queued_at` — string, необязательное, по умолчанию `""`
  - `finished_at` — string, необязательное, по умолчанию `""`
  - `seconds` — number, необязательное, по умолчанию `0.0`

**POST `/api/scanner/scan_all`** — Полный проход по всем сериалам.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `409`: `ErrorResponse` — {`success`, `error`}

**POST `/api/scanner/settings`** — Настройки планировщика (вкл/выкл, интервал).

- Тело (читает сам обработчик): `{enabled?}` — выключатель планировщика
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное

**GET `/api/scanner/status`** — Состояние планировщика сканирования.

- Ответ `200`: `ScannerStatus`
  - `scanner_enabled` — boolean, обязательное
  - `is_scanning` — boolean, обязательное
  - `next_scan_time` — string | null, необязательное
  - `next_check_time` — string | null, необязательное
  - `schedule` — ScheduleValue, обязательное
  - `checks` — string[], необязательное

**POST `/api/schedule`** — Времена суток синхронизаций и проверок торрент-раздач; расписание, которое scan отвергает, — отказ 400 с причиной.

> The times of day of the syncs and of the checks of the torrent releases; a schedule scan refuses is 400 with
> its reason.

- Тело: `ScheduleBody` (лишнее поле отклоняется — 422)
  - `schedule` — ScheduleValue, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{schedule}` — расписание как оно действует; 400 `{error}` — расписание, которое отвергает `scan`

### 6.5 Настройки, справочники, логи, БД

**GET `/api/auth`** — Креды внешних сервисов без секретов.

- Ответ `200`: `DynamicObject`
- Форма ответа: по каждому сервису (`qbittorrent`, `kinozal`, `vk`, `rutracker`) `{username, url, has_password}` (секретов нет; `{}`, если записи нет), `tmdb` — `{configured}`

**POST `/api/auth`** — Сохранение кредов сервисов.

- Тело (читает сам обработчик): `{qbittorrent?, kinozal?, rutracker?: {username, password?, url?}, vk?: {token?}, tmdb?: {token?}}`; пустой пароль или токен — «не менять»
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное

**GET `/api/auth/{service}/secret`** — Секрет одного сервиса по просьбе глаза его поля (не кэшируется); пустой, если не задан.

> The secret of one service, as the eye of its field asks it (stage 3, docs/security.md: a secret is never given
> with the credentials, only on this request, to the administrator who is signed in): {secret}, empty when none is set. Not cached.

- Ответ `200`: `DynamicObject`
- Форма ответа: `{secret}` (пусто, если не задан), `Cache-Control: no-store`; 404 — службы нет

**PUT `/api/conform/params`** — Как просить демон выравнивания: параметры целиком или коды причин отказа.

> How the daemon is asked to align: written whole or refused with coded reasons (`saved: false`).

- Тело: `ConformParams`
  - `audio_method` — string, обязательное
  - `drift_speed_pct` — number | null, необязательное
  - `keep_tmp` — boolean, обязательное
  - `fill_silence` — boolean, обязательное
  - `extend_edges` — boolean, обязательное
- Ответ `200`: `ConformSettingsSaved`
  - `saved` — boolean, обязательное
  - `problems` — SettingProblem[], обязательное
  - `settings` — ConformSettingsView, обязательное

**GET `/api/conform/settings`** — Адрес демона выравнивания, рабочая папка здесь и на демоне, очистка очереди по завершении (только чтение): как действуют сейчас.

> The address of the alignment server and the path map as they apply now.

- Ответ `200`: `ConformSettingsView`
  - `url` — string, обязательное
  - `work_dir` — string, обязательное
  - `work_dir_remote` — string, обязательное
  - `clear_finished` — boolean, обязательное
  - `params` — ConformParams, обязательное
  - `domain` — ConformDomain | null, необязательное
  - `revision` — integer, необязательное, по умолчанию `0`

**PUT `/api/conform/settings`** — Записать адрес и рабочую папку целиком (адрес применяется сразу) или получить коды причин отказа.

> Written whole and taken at once, or refused with coded reasons (`saved: false`) and nothing written.

- Тело: `ConformSettingsDraft`
  - `url` — string, обязательное
  - `work_dir` — string, обязательное
  - `work_dir_remote` — string, обязательное
- Ответ `200`: `ConformSettingsSaved`
  - `saved` — boolean, обязательное
  - `problems` — SettingProblem[], обязательное
  - `settings` — ConformSettingsView, обязательное

**POST `/api/database/clear_table`** — Очистка таблицы БД.

- Тело (читает сам обработчик): `{table_name}` (пусто — 400)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success, message}`; 500 — таблицы нет или она закрыта

**GET `/api/database/table/{table_name}`** — Все строки таблицы БД.

- Ответ `200`: `DynamicObject[]` (список)
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: список строк таблицы; 403 — таблица закрыта

**GET `/api/database/tables`** — Список таблиц БД.

- Ответ `200`: `string[]` (список)

**GET `/api/directories`** — Листинг каталогов.

- Query `path`: string, необязательный, по умолчанию `"/"`
- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: список каталогов пути (`DirectoryListing`); 403 `{error}` — путь закрыт или не читается, 400 `{error}` — иной отказ

**GET `/api/logs`** — Чтение логов из logs/*.log.

- Query `group`: string | null, необязательный
- Query `level`: string | null, необязательный
- Query `limit`: integer, необязательный, по умолчанию `200`
- Ответ `200`: `DynamicObject[]` (список)
- Форма ответа: список записей журнала (`group`, `level`, `message`, `timestamp`, ...), новые первыми

**GET `/api/logs/groups`** — Список групп логов.

> The dynamic list of the log groups: the distinct field 'group' of all the log files. The filter of the viewer takes it,
> not a static set, so as not to fall behind the real groups (the name of a group is the name of a module).

- Ответ `200`: `string[]` (список)

**GET `/api/parser-profiles`** — Список профилей парсера.

- Ответ `200`: `DynamicObject[]` (список)
- Форма ответа: список профилей с полями, которые дал `rules`

**POST `/api/parser-profiles`** — Создание профиля парсера.

- Тело (читает сам обработчик): `{name}` (пусто — 400)
- Ответ `200, 201`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: 201 `{success, id}`; 409 — отказ модуля

**PUT `/api/parser-profiles/{profile_id}`** — Переименование профиля парсера.

- Тело (читает сам обработчик): `{name}` (пусто — 400)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success}`; 409 — отказ модуля

**DELETE `/api/parser-profiles/{profile_id}`** — Удаление профиля парсера с правилами.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**GET `/api/parser-profiles/{profile_id}/rules`** — Правила профиля.

- Ответ `200`: `DynamicObject[]` (список)
- Форма ответа: список правил профиля с полями, которые дал `rules`

**POST `/api/parser-profiles/{profile_id}/rules`** — Добавление правила в профиль.

- Тело (читает сам обработчик): данные правила (раздел 6.10)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success, id}`; 400 — отказ модуля

**POST `/api/parser-rules/reorder`** — Перестановка правил.

- Тело (читает сам обработчик): массив номеров правил в новом порядке
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное

**PUT `/api/parser-rules/{rule_id}`** — Изменение правила.

- Тело (читает сам обработчик): данные правила (раздел 6.10)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}

**DELETE `/api/parser-rules/{rule_id}`** — Удаление правила.

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное

**GET `/api/provider`** — Поставщик внешних источников (Chines): адрес, состояние розетки, манифест, приложения с числом источников и линий, папка загрузок (токен — только признак наличия).

> The provider of external sources as it stands: its address, the state of the socket, what its manifest says.

- Ответ `200`: `ProviderView`
  - `url` — string, обязательное
  - `has_token` — boolean, обязательное
  - `state` — "none" | "connecting" | "up" | "down" | "incompatible", обязательное
  - `reason` — string | null, необязательное
  - `provider` — string | null, необязательное
  - `version` — string | null, необязательное
  - `protocol` — integer | null, необязательное
  - `revision` — string | null, необязательное
  - `sites` — ProviderSite[], необязательное
  - `downloads_dir` — string, необязательное, по умолчанию `""`
  - `seq` — integer, необязательное, по умолчанию `0`

**PUT `/api/provider`** — Записать адрес и токен поставщика (адрес применяется сразу, токен не задан — прежний остаётся, пустой — удаляется) или получить коды причин отказа.

> The address and the token: written whole and taken at once, or refused with coded reasons (`saved: false`).

- Тело: `ProviderSettingsDraft`
  - `url` — string, обязательное
  - `token` — string | null, необязательное
- Ответ `200`: `ProviderSaved`
  - `saved` — boolean, обязательное
  - `problems` — SettingProblem[], обязательное
  - `provider` — ProviderView, обязательное

**GET `/api/rules/line-fields`** — Коды полей строки конструктора в его порядке: из них собираются быстрые шаблоны правила «Таблица».

> The codes of the fields of a line of the constructor, in its order (LINE_FIELDS): the quick templates of a
> rule «Таблица» are made of them.

- Ответ `200`: `string[]` (список)

**GET `/api/settings/conform_clear_finished`** — Флаг очистки очереди выравнивания по завершении.

- Ответ `200`: `DynamicObject`
- Форма ответа: `{enabled}`

**POST `/api/settings/conform_clear_finished`** — Записать флаг очистки очереди выравнивания по завершении.

- Тело (читает сам обработчик): `{enabled}`
- Ответ `200`: `DynamicObject`
- Форма ответа: `{success}`

**GET `/api/settings/debug_flags`** — Структура логирования с флагами отладки.

- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{logging_modules: {<раздел>: [{name, enabled}]}}`

**POST `/api/settings/debug_flags`** — Включение/выключение отладочного логирования.

- Тело (читает сам обработчик): `{module, enabled}` (`module` обязателен, иначе 400)
- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success}`

**GET `/api/settings/download_limits`** — Сколько загрузок идёт одновременно, по способу получения файла.

> How many downloads go at once, by the way a file comes (downloads, limits.py): {threads_total,
> threads_per_file, single_at_once, provider_at_once, multi_at_once} both ways; a value the module refuses is 400 with its reason.

- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: `{threads_total, threads_per_file, single_at_once, provider_at_once, multi_at_once}`

**POST `/api/settings/download_limits`** — Записать пределы загрузок; значение, которое модуль отвергает, — отказ 400 с причиной.

> How many downloads go at once, by the way a file comes (downloads, limits.py): {threads_total,
> threads_per_file, single_at_once, provider_at_once, multi_at_once} both ways; a value the module refuses is 400 with its reason.

- Тело (читает сам обработчик): любое из `threads_total`, `threads_per_file`, `single_at_once`, `provider_at_once` (целое от 1; не присланное не меняется)
- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: пределы как они стали (как в GET); 400 `{error}` — значение отвергнуто

**GET `/api/settings/saved_paths`** — Список сохранённых путей загрузки.

> The saved download paths: the list, the addition, the removal. The source of truth is the module settings (the table
> saved_paths).

- Ответ `200`: `DynamicObject`
- Форма ответа: `{paths: [{id, path}]}`

**POST `/api/settings/saved_paths`** — Добавление сохранённого пути.

> The saved download paths: the list, the addition, the removal. The source of truth is the module settings (the table
> saved_paths).

- Тело (читает сам обработчик): `{path}` (пустая строка не добавляется)
- Ответ `200`: `DynamicObject`
- Форма ответа: `{paths}` — актуальный список

**DELETE `/api/settings/saved_paths`** — Удаление сохранённого пути.

> The saved download paths: the list, the addition, the removal. The source of truth is the module settings (the table
> saved_paths).

- Тело (читает сам обработчик): `{id}` (в теле, не в адресе)
- Ответ `200`: `DynamicObject`
- Форма ответа: `{paths}` — актуальный список

**GET `/api/settings/source_terms`** — Сроки сайтов, откуда источники читают страницы.

> The terms of the sites the sources read (sources, term.py): GET {sites: [{site, provider, sources, minutes}]}, POST
> {site, minutes} sets one; a value the sources refuse is 400 with its reason.

- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: `{sites: [{site, provider, sources, minutes}]}`

**POST `/api/settings/source_terms`** — Записать срок одного сайта; значение, которое отвергает sources, — отказ 400 с причиной.

> The terms of the sites the sources read (sources, term.py): GET {sites: [{site, provider, sources, minutes}]}, POST
> {site, minutes} sets one; a value the sources refuse is 400 with its reason.

- Тело (читает сам обработчик): `{site, minutes}` (минуты — целое от одной до месяца)
- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: `{success, minutes}`; 400 `{error}` — значение отвергнуто

**GET `/api/settings/torrent_staging`** — Папка, в которую скачиваются раздачи торрент-источников.

> The folder the releases of the torrent sources download into (torrents): {value} both ways; a value the module
> refuses is 400 with its reason.

- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: `{value}`

**POST `/api/settings/torrent_staging`** — Записать папку раздач; значение, которое отвергает torrents, — отказ 400 с причиной.

> The folder the releases of the torrent sources download into (torrents): {value} both ways; a value the module
> refuses is 400 with its reason.

- Тело (читает сам обработчик): `{value}` (абсолютный путь)
- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}
- Форма ответа: `{success, value}`; 400 `{error}` — значение отвергнуто

**GET `/api/tmdb/details/{tmdb_id}`** — Детали сериала TMDB.

- Ответ `200`: `DynamicObject`
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success, ...}` — подробности как их дала модель `metadata` (`TmdbDetails`); 404 `{success, error}` — отказ модуля

**POST `/api/tmdb/search`** — Поиск сериала в TMDB.

- Тело (читает сам обработчик): `{query}` (пустой — 400)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 409, 500`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{success, results}`; 400 — отказ модуля

**GET `/api/trackers`** — Список трекеров с зеркалами.

- Ответ `200`: `DynamicObject[]` (список)
- Форма ответа: список трекеров с зеркалами (`Tracker`)

**PUT `/api/trackers/{tracker_id}`** — Обновление зеркал трекера.

- Тело (читает сам обработчик): `{mirrors}` (обязательно, иначе 400)
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 403, 404, 500`: `ErrorOnly` — {`error`}

**GET `/api/voices/players`** — Каждый плеер порядка источников и его место среди источников сериалов.

> Every player of the order of sources with where it is among the sources of the series.

- Ответ `200`: `PlayersListReply`
  - `players` — PlayerFacts[], необязательное

**GET `/api/voices/settings`** — Группы озвучек и порядок источников: как действуют, порядок по умолчанию.

> What applies now; the screen shows exactly this.

- Ответ `200`: `VoiceSettingsView`
  - `priority` — string[], обязательное
  - `players_off` — string[], необязательное
  - `voice_groups` — object<string[]>, обязательное
  - `voice_languages` — object<string>, необязательное
  - `default_priority` — string[], обязательное
  - `rejected` — string[], обязательное
  - `player_labels` — object<string>, необязательное
  - `player_groups` — object<string[]>, необязательное
  - `cut_max_height` — integer, необязательное, по умолчанию `0`

**PUT `/api/voices/settings`** — Записать группы и порядок источников целиком или получить коды причин отказа.

> Written whole or refused with coded reasons (`saved: false`); a refusal writes nothing.

- Тело: `VoiceSettingsDraft`
  - `priority` — string[], обязательное
  - `players_off` — string[], необязательное
  - `voice_groups` — object<string[]>, обязательное
  - `voice_languages` — object<string>, необязательное
- Ответ `200`: `VoiceSettingsSaved`
  - `saved` — boolean, обязательное
  - `problems` — SettingProblem[], обязательное
  - `settings` — VoiceSettingsView, обязательное

### 6.6 Служебные

**POST `/api/_diag/echo`** — Диагностический прокси к шине (только при diag=True).

- Тело (читает сам обработчик): `{topic, payload?, timeout?}`
- Ответ `200`: `{reply}` — ответ шины; 502 — отказ

**GET `/api/stream`** — SSE-поток событий шины.

- Query `frames`: "each" | "batch", необязательный, по умолчанию `"each"`
- Ответ `200`: поток `text/event-stream`: кадр на событие шины (`frames=each`, по умолчанию) или на ход шины (`frames=batch`); раздел 7

### 6.7 Источники

**POST `/api/parser-profiles/test`** — Тест профиля правил на странице: строки для конструктора и ответ на каждую (composition.preview).

> The test of a profile: the same preview of a page, with the profile given, so that every line comes
> with the constructor's answer.

- Тело: `RulesTestRequest` (лишнее поле отклоняется — 422)
  - `url` — string, необязательное, по умолчанию `""`
  - `profile_id` — integer, обязательное
  - `series_season` — string | integer | null, необязательное
  - `refresh` — boolean, необязательное, по умолчанию `true`
  - `lines` — string[] | null, необязательное
- Ответ `200`: `CompositionPreviewReply`
  - `url` — string, обязательное
  - `state` — TableState, обязательное
  - `reason` — string | null, необязательное
  - `names` — string[], необязательное
  - `summary` — SourceSummary | null, необязательное
  - `lines` — PreviewLine[], необязательное
  - `rows` — CompositionRow[], необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**GET `/api/series/{series_id}/sources`** — Источники сериала по порядку.

- Ответ `200`: `SeriesSource[]` (список)
  - `id` — integer, обязательное
  - `series_id` — integer, обязательное
  - `position` — integer, необязательное, по умолчанию `0`
  - `url` — string, обязательное
  - `source_type` — SourceType, обязательное
  - `parser_profile_id` — integer | null, необязательное, по умолчанию `null`
  - `vibix_season` — integer | null, необязательное, по умолчанию `null`
  - `players_off` — string[], необязательное
  - `rows_per_line` — string, необязательное, по умолчанию `"all"`
  - `color` — integer, необязательное, по умолчанию `0`
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/sources`** — Добавление источника сериалу (ответ {id}).

- Тело: `SourceAdd` (лишнее поле отклоняется — 422)
  - `url` — string, обязательное
  - `source_type` — SourceType, обязательное
  - `parser_profile_id` — integer | null, необязательное
  - `vibix_season` — integer | null, необязательное
  - `players_off` — string[], необязательное
  - `rows_per_line` — string | null, необязательное
  - `position` — integer | null, необязательное
- Ответ `200`: `CreatedSource`
  - `id` — integer, обязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/sources/{source_id}`** — Изменение полей источника: адрес, профиль, режим поиска, сезон Vibix, отключённые плееры, место.

- Тело: `SourceChange` (лишнее поле отклоняется — 422)
  - `url` — string | null, необязательное
  - `parser_profile_id` — integer | null, необязательное
  - `vibix_season` — integer | null, необязательное
  - `players_off` — string[] | null, необязательное
  - `rows_per_line` — string | null, необязательное
  - `position` — integer | null, необязательное
- Ответ `200`: `ChangedSource`
  - `success` — boolean, обязательное
  - `applied` — object, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**DELETE `/api/series/{series_id}/sources/{source_id}`** — Удаление источника (единственный не удаляется: last_source).

- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/sources/cancel`** — Остановить идущее чтение страницы.

> Stop the read of the page going now (the user stopped it on the card of the source): it ends failed, the last
> table serves, and it is not begun again at the start.

- Тело: `TableReadRequest` (лишнее поле отклоняется — 422)
  - `url` — string, обязательное
  - `rows` — string | null, необязательное
- Ответ `200`: `ReadCancelled`
  - `success` — boolean, обязательное
  - `cancelled` — boolean, необязательное, по умолчанию `false`
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**POST `/api/sources/fresh`** — Когда прочитаны таблицы страниц и до какого срока они свежи.

> When the tables of the pages were read and until when they are current; the events `sources_table_ready` and
> `sources_table_fresh` carry it on.

- Тело: `FreshRequest` (лишнее поле отклоняется — 422)
  - `urls` — string[], обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}
- Форма ответа: `{pages: [{url, read_at, fresh_until}]}` — когда прочитана таблица каждой страницы и до какого срока она свежа; страница без таблицы не названа

**POST `/api/sources/preview`** — Что показать окну об адресе, который ещё не источник: состояние таблицы, сводка, строки для конструктора.

> What a window shows of an address before it is a source: the summary of its table and its lines.

- Тело: `CompositionPreviewRequest`
  - `url` — string, необязательное, по умолчанию `""`
  - `profile_id` — integer | null, необязательное
  - `series_season` — string | integer | null, необязательное
  - `refresh` — boolean, необязательное, по умолчанию `true`
  - `lines` — string[] | null, необязательное
  - `players_off` — string[], необязательное
  - `vibix_season` — integer | null, необязательное
- Ответ `200`: `CompositionPreviewReply`
  - `url` — string, обязательное
  - `state` — TableState, обязательное
  - `reason` — string | null, необязательное
  - `names` — string[], необязательное
  - `summary` — SourceSummary | null, необязательное
  - `lines` — PreviewLine[], необязательное
  - `rows` — CompositionRow[], необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**POST `/api/sources/read`** — Прочитать страницу сейчас, не дожидаясь срока её таблицы.

> Read the page now, whatever the age of its table (the hour of a table is the term of a sync, not of a user).

- Тело: `TableReadRequest` (лишнее поле отклоняется — 422)
  - `url` — string, обязательное
  - `rows` — string | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**POST `/api/sources/resolve`** — Какой сайт читает адрес и его канонический вид (без сети).

> Which site reads an address, and the address in its canonical form; no network.

- Тело: `SiteResolveRequest`
  - `url` — string, обязательное
- Ответ `200`: `SiteResolveReply`
  - `site` — string | null, необязательное
  - `canonical_url` — string | null, необязательное
  - `source_type` — string | null, необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**POST `/api/sources/search`** — Поиск названий у поставщика внешних источников: страница названий как он её даёт.

> The search of the titles of the provider of external sources: a page of them as it gives them; a title is a source by
> its address.

- Тело: `TitleSearchRequest`
  - `query` — string, обязательное
  - `page` — integer, необязательное, по умолчанию `1`
- Ответ `200`: `TitleSearchReply`
  - `titles` — TitleCard[], необязательное
  - `page` — integer, необязательное, по умолчанию `1`
  - `pages` — integer | null, необязательное
  - `total` — integer | null, необязательное
  - `more` — boolean | null, необязательное
  - `without_lines` — integer, необязательное, по умолчанию `0`
  - `notes` — string[], необязательное
  - `reason` — string | null, необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

**GET `/api/sources/settings`** — Настройки источников: зеркала каждого трекера и домены, страницы которых читает сервис.

> The settings screen of the sources: the mirrors of each tracker, and the domains the service reads pages of
> now — the trackers with their mirrors, the sites of the registry.

- Ответ `200`: `SourcesSettingsView`
  - `trackers` — TrackerMirrors[], необязательное
  - `readers` — ParsedDomains[], необязательное
- Ответ `400, 500, 503`: `ErrorOnly` — {`error`}

### 6.8 Выход

**GET `/api/series/{series_id}/composition`** — Раскладка видео-сериала (composition).

> The composition window: the layout of the series (`composition`, rows the constructor excluded only with
> `include_excluded`; `sources_only` leaves out the rows, levels and settings). `part` is what of it is asked
> (`LayoutPart`: a window reads again only what a change touched), `episodes` — `season:episode` of the part
> `levers`.

- Query `include_excluded`: boolean, необязательный, по умолчанию `false`
- Query `sources_only`: boolean, необязательный, по умолчанию `false`
- Query `part`: "whole" | "levers" | "sources", необязательный, по умолчанию `"whole"`
- Query `episodes`: string[], необязательный, по умолчанию `[]`
- Ответ `200`: `CompositionReply`
  - `series_id` — integer, обязательное
  - `rev` — integer, обязательное
  - `part` — "whole" | "levers" | "sources", необязательное, по умолчанию `"whole"`
  - `rows` — CompositionRow[], необязательное
  - `subtitles` — CompositionRow[], необязательное
  - `sources` — SourceSummary[], необязательное
  - `levels` — LevelSettings[], необязательное
  - `settings` — EpisodeSettings[], необязательное
  - `levers` — LeverSpec[], необязательное
  - `availability` — object<string[]>, необязательное
  - `voice_orders` — SeasonVoiceOrder[], необязательное
  - `voice_titles` — object<string>, необязательное
  - `compilations` — CompilationView[], необязательное
- Ответ `404, 500`: `ErrorOnly` — {`error`}

**GET `/api/series/{series_id}/output`** — Референс каждой серии и отметка (planner).

> The reference of every episode of the series and why it is that one (the mark).

- Ответ `200`: `Reference[]` (список)
  - `season` — integer, обязательное
  - `episode` — integer, обязательное
  - `part` — integer | null, необязательное, по умолчанию `null`
  - `mark` — ReferenceMark, обязательное
  - `source_id` — integer | null, необязательное, по умолчанию `null`
  - `row_key` — string | null, необязательное, по умолчанию `null`
  - `player` — string | null, необязательное, по умолчанию `null`
  - `voice_group` — string | null, необязательное, по умолчанию `null`
  - `height` — integer | null, необязательное, по умолчанию `null`
  - `reason` — string | null, необязательное, по умолчанию `null`
  - `compilation` — CompilationRef | null, необязательное, по умолчанию `null`
  - `picture` — CutPicture | null, необязательное, по умолчанию `null`
  - `markup` — boolean, необязательное, по умолчанию `false`
- Ответ `404, 500`: `ErrorOnly` — {`error`}

**GET `/api/series/{series_id}/output/queues`** — Очереди референса и озвучек групп уровня в порядке ядра, со значениями рычагов.

> The queue of the reference and of the voice-over of each group of a level (the series, a season, a compilation
> of a season, or an episode of a season), in the order the core tries their variants, with what the levers of the
> level say of each.

- Query `season`: integer | null, необязательный
- Query `episode`: integer | null, необязательный
- Query `compilation`: string | null, необязательный
- Ответ `200`: `QueuesReply`
  - `series_id` — integer, обязательное
  - `season` — integer | null, необязательное
  - `episode` — integer | null, необязательное
  - `compilation` — string | null, необязательное
  - `reference` — QueueView, необязательное
  - `groups` — GroupQueue[], необязательное
  - `markup` — QueueView, необязательное
  - `markup_follows` — boolean, необязательное, по умолчанию `true`
  - `subtitles` — QueueView, необязательное
- Ответ `404, 500`: `ErrorOnly` — {`error`}

**PUT `/api/series/{series_id}/output/settings`** — Значение рычага выхода на уровне сериала, сезона или серии.

> Sets the value of a lever at a level (the series, a season or an episode); a lever, a target or a value
> that the registry does not know is refused with its code (`unknown_lever`, `bad_target`, `bad_value`).

- Тело: `OutputSettingsSet` (лишнее поле отклоняется — 422)
  - `season` — integer | null, необязательное
  - `episode` — integer | null, необязательное
  - `compilation` — string | null, необязательное
  - `lever` — string, обязательное
  - `target` — string | null, необязательное
  - `value` — any, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/output/settings/batch`** — Несколько рычагов одной записью: галочки очереди, «снять все».

> Sets several levers in one write: the boxes the user ticked, or all of a queue's off. One count of the plan
> follows for all of them; one item the registry refuses refuses all, with its code, and nothing is stored.

- Тело: `OutputSettingsBatch` (лишнее поле отклоняется — 422)
  - `items` — OutputSettingsSet[], обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/output/settings/reset`** — Возврат рычагов уровня в наследование.

> Takes the levers of a level (or the one named, or those of the queue named) back to the level above.

- Тело: `OutputSettingsReset` (лишнее поле отклоняется — 422)
  - `season` — integer | null, необязательное
  - `episode` — integer | null, необязательное
  - `compilation` — string | null, необязательное
  - `lever` — string | null, необязательное
  - `target` — string | null, необязательное
  - `queue` — string | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/output/switch`** — Отключить или включить серии сезона списком, а сезон целиком — без списка.

> Switches an episode (or, without `episode`, a whole season) off or on. What the output shows of it comes
> as references: the episode gets the mark `off`, or its reference back.

- Тело: `OutputSwitchBody` (лишнее поле отклоняется — 422)
  - `season` — integer, обязательное
  - `episodes` — integer[] | null, необязательное
  - `off` — boolean, обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/output/voice-order`** — Порядок озвучек сезона, заданный пользователем: порядок дорожек в контейнерах.

> The user's order of the voice-overs of a season: the order of the tracks in its containers. A plan: the
> containers follow it at the next sync; refused while a sync of the series is under way.

- Тело: `VoiceOrderBody` (лишнее поле отклоняется — 422)
  - `season` — integer, обязательное
  - `labels` — string[], обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**PUT `/api/series/{series_id}/output/voice-title`** — Название дорожки группы озвучек в контейнерах сериала.

> The user's name of the track of a voice-over group in the containers of the series. A plan: the containers
> take it at the next sync; refused while a sync of the series is under way.

- Тело: `VoiceTitleBody` (лишнее поле отклоняется — 422)
  - `label` — string, обязательное
  - `title` — string, обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**GET `/api/series/{series_id}/plan`** — План видео-сериала: актив с происхождением каждого файла, цель каждой серии, неудачи и ждущие команды «Удалить».

> The plan of a video series (modules/planner): the asset with the provenance of every file and whether its row is
> online, the goal of every episode as the last sync counted it, the failures and the commands «Удалить» that wait.

- Ответ `200`: `PlannerView`
  - `series_id` — integer, обязательное
  - `running` — boolean, необязательное, по умолчанию `false`
  - `references` — Reference[], необязательное
  - `assets` — PlanAsset[], необязательное
  - `episodes` — PlanEpisode[], необязательное
  - `failures` — PlanFailure[], необязательное
  - `problems` — PlanProblem[], необязательное
  - `commands` — PlanCommand[], необязательное
- Ответ `404, 500`: `ErrorOnly` — {`error`}

**POST `/api/series/{series_id}/plan/delete`** — «Удалить» файла или дорожки в нём: исполнит следующая синхронизация.

> «Удалить» of a file or of a track in it: carried out by the next sync. A video goes only for another video of its
> episode; with none the file stays and the command waits.

- Тело: `PlanDeleteBody`
  - `kind` — "video" | "track", обязательное
  - `name` — string, обязательное
  - `dub` — PlanRowRef | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/series/{series_id}/plan/delete/cancel`** — Вернуть ждущую команду «Удалить».

> The command «Удалить» that waits is taken back.

- Тело: `PlanDeleteBody`
  - `kind` — "video" | "track", обязательное
  - `name` — string, обязательное
  - `dub` — PlanRowRef | null, необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 500, 503`: `ErrorResponse` — {`success`, `error`}

### 6.9 Торрент-источник

**POST `/api/torrent/check`** — Прочитать страницу сейчас: отслеживаемая раздача в другой версии начинает обновление.

> The page is read now: a watched release it lists in another version begins its update, or waits for a sync.

- Тело: `PageBody`
  - `url` — string, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: раздачи, у которых началось обновление или оно ждёт синхронизации (`ReleasesChecked`)

**POST `/api/torrent/download`** — «Скачать»: раздача уходит в клиент в свою папку.

> «Скачать»: the release goes to the client into its folder; a refusal says why (409).

- Тело: `ReleaseBody`
  - `url` — string, обязательное
  - `release` — string, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: раздача после начала загрузки (`ReleaseView`)

**POST `/api/torrent/editor`** — Редактор торрента страницы: элементы пяти вкладок, папки, неразмещённое, профили.

> The torrent editor of the page: the items of its five tabs, the folders, what is unplaced, the profiles. The first
> opening of a page makes its profiles; the editor itself is a read.

- Тело: `PageBody`
  - `url` — string, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: `{url, profiles, view}` — редактор страницы: профили пяти вкладок и раскладка (`view`: раздачи с ходом чтения их файлов, элементы вкладок, папки, неразмещённое); при первом открытии страницы профили создаются

**POST `/api/torrent/editor/assign`** — Разместить вручную: файлы — по сезону и серии, дорожки — по папке.

- Тело: `AssignBody`
  - `url` — string, обязательное
  - `tab` — "files" | "voices" | "subtitles" | "voice_streams" | "subtitle_streams", обязательное
  - `items` — string[], обязательное
  - `season` — integer | null, необязательное
  - `episode` — integer | null, необязательное
  - `episode_last` — integer | null, необязательное
  - `tag` — string, необязательное, по умолчанию `""`
  - `folder` — string | null, необязательное
  - `in_order` — boolean, необязательное, по умолчанию `false`
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/editor/auto`** — «Авто»: автоматическая раскладка снова делает удалённые папки и размещает снятые дорожки.

- Тело: `AutoBody`
  - `url` — string, обязательное
  - `tabs` — "voices" | "subtitles" | "voice_streams" | "subtitle_streams"[], обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/editor/folder`** — Создать или изменить папку дорожек.

- Тело: `FolderBody`
  - `url` — string, обязательное
  - `tab` — "voices" | "subtitles" | "voice_streams" | "subtitle_streams", обязательное
  - `name` — string, обязательное
  - `language` — string, необязательное, по умолчанию `""`
  - `subtitle_type` — "" | "full" | "signs", необязательное, по умолчанию `""`
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/editor/folder/delete`** — Удалить папку дорожек.

- Тело: `FolderRef`
  - `url` — string, обязательное
  - `tab` — "voices" | "subtitles" | "voice_streams" | "subtitle_streams", обязательное
  - `name` — string, обязательное
  - `automatic` — boolean, необязательное, по умолчанию `false`
  - `items` — string[], необязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/editor/remove`** — «−»: снять дорожки с их папок.

- Тело: `RemoveBody`
  - `url` — string, обязательное
  - `tab` — "voices" | "subtitles" | "voice_streams" | "subtitle_streams", обязательное
  - `items` — string[], обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/editor/unassign`** — Снять ручное размещение.

- Тело: `UnassignBody`
  - `url` — string, обязательное
  - `tab` — "files" | "voices" | "subtitles" | "voice_streams" | "subtitle_streams", обязательное
  - `items` — string[], обязательное
- Ответ `200`: `OkResponse`
  - `success` — boolean, обязательное
  - `message` — string | null, необязательное
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}

**POST `/api/torrent/page`** — Что перечисляет страница трекера сейчас (чтение с трекера).

> What the page of the tracker lists now (read from the tracker): every release with its id on the page.

- Тело: `PageBody`
  - `url` — string, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: что перечисляет страница трекера (`PageReleases`)

**POST `/api/torrent/releases`** — Раздачи, которые держит torrents для страницы: состояние, версия, папка, файлы.

> The releases kept for the page: state, version, folder, files.

- Тело: `PageBody`
  - `url` — string, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: раздачи, которые держит `torrents` для страницы (`ReleasesReply`)

**POST `/api/torrent/watch`** — Включить или выключить автоматическое обновление раздачи.

> The automatic update of the release on or off.

- Тело: `WatchBody`
  - `url` — string, обязательное
  - `release` — string, обязательное
  - `watched` — boolean, обязательное
- Ответ `200`: `DynamicObject`
- Ответ `400, 404, 409, 500, 503`: `ErrorResponse` — {`success`, `error`}
- Форма ответа: раздача после смены слежения (`ReleaseView`)

### 6.10 Конструктор правил: устройство и словарь

Состояние конструктора — правила профиля. Пять операций над ними (подробно — 6.5):

| Операция в конструкторе | Метод | Путь |
|---|---|---|
| Прочитать правила профиля | GET | `/api/parser-profiles/{profile_id}/rules` |
| Создать правило | POST | `/api/parser-profiles/{profile_id}/rules` |
| Изменить правило | PUT | `/api/parser-rules/{rule_id}` |
| Удалить правило | DELETE | `/api/parser-rules/{rule_id}` |
| Изменить порядок правил | POST | `/api/parser-rules/reorder` |

Перетаскивание блоков внутри шаблона (`vuedraggable` в
`ParserPatternEditor.vue`) — операция только на стороне клиента: блоки
собираются в список, при сохранении сериализуются в `pattern` условия или в
`action_pattern` действия и уходят в тело `POST`/`PUT` правила. Порядок
правил в панели меняется стрелками «вверх/вниз» (`moveRule`), и уходит в
`POST /api/parser-rules/reorder` массивом id.

**Правило** (тело `POST`/`PUT`; хранится в `parser_rules` и
`parser_rule_conditions`, модуль `rules`). При `PUT` не присланное поле не
меняется:

| Поле | Смысл |
|---|---|
| `name` | название; при создании по умолчанию «Новое правило» |
| `conditions` | список «ЕСЛИ» `{condition_type, pattern, logical_operator}`; пустой — правило подходит любой строке; при `PUT` `[]` очищает условия |
| `action_pattern` | JSON-строка со списком действий «ТО», по умолчанию `[]` |
| `continue_after_match` | продолжить разбор строки после этого правила; по умолчанию нет: правило, давшее результат, останавливает разбор |
| `rule_table` | объект правила «Таблица» или `null` у обычного правила |

В ответе `GET …/rules` у правила, кроме этих полей, есть `id`, `profile_id`,
`priority`, у каждого условия — `id`, и отметки `valid` (`false` — движок
правило не скомпилирует) и `error` (причина). Правило со значением
`valid: false` не применяется: оно названо в `invalid_rules` каждого ответа
`rules.apply` и записано в журнал модуля `rules`.

**`condition_type`** — «ЕСЛИ». Список закрыт (`CONDITION_TYPES` движка):

| Значение | Смысл |
|---|---|
| `contains` | шаблон найден в названии |
| `not_contains` | шаблон не найден |

**`logical_operator`** условия — `AND` (по умолчанию) или `OR`. `OR`
закрывает группу «И» после этого условия: правило подходит строке, если
целиком истинна хотя бы одна группа.

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
| `range` | диапазон номеров: `1`, `3-10`, `1-180`, `1, 3-10` (`value`) | **только ЕСЛИ** |
| `add` | прибавить `value` к предыдущему числу | **только ТО** |
| `subtract` | вычесть `value` из предыдущего числа | **только ТО** |
| `whitespace` | один или несколько пробелов | ЕСЛИ, ТО |
| `any_text` | любой текст (нежадный поиск) | ЕСЛИ, ТО |
| `start_of_line` | привязка к началу названия | ЕСЛИ, ТО |
| `end_of_line` | привязка к концу названия | ЕСЛИ, ТО |
| `or` | «или» между двумя `text`: одно место названия, где читается любой из текстов | ЕСЛИ, ТО |

Список закрыт (`BLOCK_TYPES` движка). Блок «или» стоит между двумя блоками
`text`, текст группы не пуст.

**Блоки-приложения** (`range`, `add`, `subtract`) — хвосты числа: они
фильтруют или меняют число, за которым стоят. Правила одинаковы для всех
трёх видов:

- блок-приложение **один на шаблон**;
- в шаблоне с блоком-приложением **ровно одно число**, и блок стоит сразу за
  ним — иначе он указывал бы на неоднозначное число;
- числа в шаблоне нет — блок-приложение недопустим;
- `range` допустим только в ЕСЛИ, `add`/`subtract` — только в ТО; значение
  `add`/`subtract` — целое число.

Смысл «Диапазона» при одном числе: он фильтрует это число («серия не в
1-180»), а компиляция «10-30» определяется по самому названию. Явный шаблон
компиляции из двух чисел с «Диапазоном» недопустим — ровно тот случай, где
блок указывал бы сразу на два числа.

**`action_pattern`** — JSON-**строка** со списком действий, каждое действие
— `{action_type, action_pattern}`; вложенный `action_pattern` — снова
JSON-строка со списком блоков (у `extract_*`) или строка значения (у
`assign_*`):

```json
[{"action_type": "extract_single", "action_pattern": "[{\"type\":\"number\"}]"},
 {"action_type": "assign_voiceover", "action_pattern": "LostFilm"}]
```

| `action_type` | Смысл | `action_pattern` |
|---|---|---|
| `exclude` | исключить видео | пустая строка |
| `extract_single` | извлечь номер серии | блоки (`number`) |
| `extract_range` | извлечь диапазон серий | блоки (число начала и число конца) |
| `extract_season` | установить номер сезона | блоки |
| `extract_part` | извлечь часть серии | блоки — принимается движком, панель его не предлагает |
| `assign_voiceover` | назначить озвучку/тег | строка значения |
| `assign_episode` | назначить номер серии | целое число |
| `assign_season` | назначить номер сезона | целое число |
| `assign_part` | назначить часть серии | целое число |
| `assign_quality` | назначить качество | строка значения |
| `assign_resolution` | назначить разрешение | строка значения |
| `assign_range` | назначить диапазон серий | `A-B`, где A меньше B |
| `assign_video_tag` | назначить тег видео (версия серии) | строка значения |
| `assign_voice_folder` | назвать каталог озвучки | имя или JSON `{name, language, type}` |
| `assign_subtitle_folder` | назвать каталог субтитров | имя или JSON `{name, language, type}` |
| `assign_language` | назначить язык дорожки | строка значения |
| `assign_subtitle_type` | назначить вид субтитров | `full` или `signs` |

Список закрыт (`ACTION_TYPES` движка): любой другой тип отклоняется при
записи правила. Пять последних действий (`assign_video_tag`,
`assign_voice_folder`, `assign_subtitle_folder`, `assign_language`,
`assign_subtitle_type`) — действия редактора раздачи (`EDITOR_ASSIGN`
движка): каждое кладёт значение в свой ключ результата (`video_tag`,
`voice_folder`, `subtitle_folder`, `language`, `subtitle_type`). Внутри одного правила несколько действий `extract_*` одного
ключа — варианты по порядку (выигрывает первый сработавший), между правилами
позднее правило перекрывает раннее.

**`rule_table`** — правило «Таблица»: строки, каждая из которых — обычное
правило с одним условием «содержит» и своим действием.

```json
{"action_type": "assign_voiceover",
 "rows": [{"if": [{"type": "text", "value": "LostFilm"}], "then": "LostFilm"}]}
```

| Поле | Смысл |
|---|---|
| `action_type` | действие колонки «ТО» (из `ACTION_TYPES`) |
| `language` | необязательно: язык для строк `assign_voice_folder` / `assign_subtitle_folder`, у которых своего нет |
| `rows[].if` | блоки шаблона «ЕСЛИ» строки, не пусто |
| `rows[].then` | значение «ТО» (строка для `assign_*`, блоки для `extract_*`; для `exclude` не нужно) |
| `rows[].action_type` | необязательно: действие строки вместо колонки |
| `rows[].if_joined` | строка — ещё одно «ТО» условия строки выше |

Остальные поля строки (например `then_joined`) движок не читает: это запись
редактора. Пустая таблица, строка без «ЕСЛИ» и строка `assign_*` без значения
отклоняются при записи.

**`priority`** — поле присутствует в ответе `GET
/api/parser-profiles/{profile_id}/rules` и определяет порядок применения
правил. Задать его напрямую нельзя: `POST` правила присваивает
`priority = max + 1`, `PUT` правила поле не трогает. Порядок меняется
только через `POST /api/parser-rules/reorder`, который проставляет
`priority` равным индексу в переданном массиве.

**Отказ записи.** Правило, которое движок не скомпилирует, `POST`/`PUT`
отклоняют кодом 400 `{"success": false, "error": <причина>}`: причина — код
`rule_invalid` с `detail` (код причины движка и его параметры); словами её
излагает интерфейс (`web/src/i18n/ru.ts`). Проверка выполняется тем же
компилятором, что и применение профиля, — отдельного валидатора нет, чтобы
проверка и исполнение не разошлись (`validate_rule`, `calc/rules/engine.py`).
Отклоняются: неизвестный `condition_type`, неизвестный `action_type`, неизвестный
тип блока, блок `range` в действии «ТО», блок `±` в условии «ЕСЛИ», два
блока-приложения в одном шаблоне, блок-приложение не сразу за единственным
числом, нечисловое значение блока `±`, пустой шаблон, «или» не между двумя
текстами, диапазон `assign_range` не вида `A-B`, `pattern` или
`action_pattern`, не являющиеся JSON.

Пробный разбор страницы профилем без записи — `POST /api/parser-profiles/test`
(6.7): каждая строка приходит с ответом конструктора.

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
# перегенерировать реестр endpoints.json и разделы 1, 3, 5, 6 этого файла из живого приложения
.venv/bin/python scripts/gen_endpoints.py

# проверить, что справочник не отстал от кода (ENDPOINTS_OK / ENDPOINTS_STALE)
.venv/bin/python scripts/gen_endpoints.py --check

# поднять приложение с включённой документацией
ST_DOCS_ENABLED=true .venv/bin/uvicorn run:app --port 5000

# снять спецификацию
curl -s http://127.0.0.1:5000/openapi.json -o /tmp/openapi.json

# перегенерировать типы фронта
cd web && npm run gen:api
```

Типы фронта лежат в `web/src/api/schema.d.ts`; они пересоздаются из схемы живого приложения. Команда `npm run gen:api` берёт схему у экземпляра
на `:5000`; стенд на свободном порту поднимается `uvicorn run:app` с `ST_DOCS_ENABLED=true` на новой БД.
