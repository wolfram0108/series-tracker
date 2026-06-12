# Состояние пересоздания — точка продолжения

> Обновлено: 2026-06-12. Этот файл — снимок «где мы» для продолжения
> работы после сжатия контекста. Правила работы — в [CLAUDE.md](../CLAUDE.md)
> (целеполагание!), решения — в [contracts/revision.md](../contracts/revision.md)
> (Р-1..Р-11), находки — в [contracts/findings.md](../contracts/findings.md)
> (1–25), план — в [docs/refactoring_bus_plan.md](refactoring_bus_plan.md).

## Выполнено (этапы 0–3 из 6)

| Этап | Состояние |
|---|---|
| 0. Инвентаризация | ✓ реестр 73 эндпоинтов (contracts/endpoints.md), golden GET-снимки и фикстура БД в tests/ (вне git), SSE-запись в ~/st-bus-stage0 (остановлена по требованию пользователя — прод больше не трогаем) |
| 1. Каркас | ✓ core/ (шина+конверт+BaseModule+раннер+логирование), gateway-скелет (FastAPI, SSE_MAP), run.py |
| 2. Инфраструктура | ✓ torrents (qBit-клиент: локальный infohash вкл. гибриды v2, два поколения API, релогин на 403), trackerauth (fetch-прокси, персистентные сессии, rate-limit), settings, metadata (TMDB), library (листинг), Alembic (0001 базовая схема 19 живых таблиц + 0002 tracker_sessions), core/db.py |
| 3. Мозги | ✓ rules (движок с нуля, фиксы А/Б/В), sources (Kinozal/RuTracker/Anilibria-API/VK-API; astar+anilibria_tv — разбор готов, браузерная доставка на этапе 6), scan/planner.py (SmartCollector v4.1 + фикс Г) |
| 4. Конвейер (идёт) | ✓ статусная модель (Р-11): modules/catalog — агрегатор свёрток, series.status.changed только при изменениях, эфемерный viewing со страховкой gateway.sse.clients; находки 23–25. ✓ scan-оркестратор (Р-12): зеркала в sources, настоящая ресьюмабельность scan_tasks, формулы id верифицированы (190/190, 351/351), расписание автоскана, отказ параллельному скану; core: handle(concurrent=True); находки 26–29. ✓ downloads (Р-13): событийный диспетчер yt-dlp (async subprocess), ретрай ошибок сканом, fs.sync вместо 60-секундной ФС-проверки, агрегатор: waiting подавляется активностью; находки 30–32. ✓ торрент-конвейер (Р-14): ИНВАРИАНТ ЯДРА «пауза до конца переименования, magnet — запуск ровно на метаданные», чистая машина стадий (старые значения в БД), ошибки-носители stage='error', реализованы все контракты Р-12, адаптивный мониторинг прогресса, fs.verify. ✓ renaming + форматтер (Р-15): форматтер в rules с нуля, дифф имён 349/349, reprocess/process_torrent, событие renaming.finished, single_vk похоронен (находка 33); находки 33–34. ✓ slicing (Р-16): порт 1:1 (главы/фильтр/нарезка/verify/deep-adoption), ffmpeg+yt-dlp с таймаутами, ресьюмабельность progress_chapters, закрыты фейки Р-15; находка 35 |

**Верификации против старого кода (все локально, прод не участвует):**
infohash 170/170 реальных торрентов; движок правил 1088/1088 реальных
названий; планировщик 9/10 сериалов + 1 согласованное отклонение
(сериал 87, фикс Г). Тесты: 72 passed (`.venv/bin/python -m pytest -q`),
интеграция со стендовым qBit — `ST_QBIT_URL=http://series-tracker:8080
ST_QBIT_USER=admin ST_QBIT_PASS=REMOVED-SECRET pytest tests/test_torrents_integration.py`.
Дифф-харнесы: `tests/verify_rules_diff.py`, `tests/verify_planner_diff.py`.

## СЛЕДУЮЩИЙ ШАГ (точка продолжения)

**Этап 4 продолжается.** Статусная модель готова (Р-11): статус —
вычисляемое значение; series.state и series_statuses новая система не
читает и не пишет. ОБЯЗАТЕЛЬСТВО для каждого следующего модуля этапа 4:
публиковать свёртку `series.status.contribution {source, series_id,
flags}` (а) при старте после своего reconcile, (б) при каждом изменении
вклада (все false = вклад снят); ошибки — носителем в задаче
(stage/status='error', задачу не удалять; для скана —
scan_tasks.status='error').

Scan готов (Р-12). Контракты, которые scan ЖДЁТ от следующих модулей
(зафиксированы фейками tests/test_scan_module.py): renaming.reprocess
(query); torrents.db.active, torrents.db.deactivate_all,
torrents.register (идемпотентен: upsert по torrent_id + agent_task +
замена старого: деактивация + удаление из qBit), torrents.queue.get,
событие torrents.queue.changed {count}; событие scan.plan.updated →
downloads (создание download_tasks + усыновление файлов — его зона).

Downloads готов (Р-13). Контракты, которые ЖДУТ реализации другими
модулями: rules.format_filename {series, media_item} → {filename}
(фейк в tests/test_downloads_module.py; реализация — с разбором
renaming/rules); gateway на этапе 5: открытие модалки статуса →
query downloads.fs.sync {series_id} (решение пользователя — вместо
фоновой 60-секундной ФС-проверки); SSE-маппинг
downloads.queue.changed → download_queue_update.

Slicing готов (Р-16) — ВСЕ межмодульные контракты-фейки этапа 4
закрыты, конвейер замкнут. Gateway этап 5: открытие модалки →
downloads.fs.sync + torrents.fs.verify; сохранение свойств сериала →
renaming.reprocess (+relocation при смене пути); SSE-маппинги:
queue.changed (torrents/downloads/slicing) → agent/download/
slicing_queue_update, renaming.finished → renaming_complete,
series.status.changed → series_updated, scan.status.changed →
scanner_status_update.

Остался последний модуль этапа 4: library (relocation_tasks —
перемещение VK-файлов на диске и торрентов через set_location;
файловый браузер уже есть с этапа 2 — расширить relocation'ом;
is_busy = relocation + renaming.tasks.active — решить представление
при разборе; событие library.relocation.started/finished — SSE
relocation_started/finished). Находка 7г: все агентские кандидаты
закрыты превентивно таймаутами; финальная сверка журнала WORKER
TIMEOUT с логами приложения — на этапе 6 (стенд).

После: этап 5 (gateway: все 73 точки с ревизией «подтверждена/
перепроектирована/удалена» в contracts/revision.md; правки JS-слоя
фронта — каждая по согласованию, визуал неприкосновенен), этап 6
(стенд: учётки из /home/user/series-tracker/docs/key.txt — ещё НЕ
читал; Playwright для astar/anilibria_tv; авторизованный дамп
RuTracker; эмуляция сценариев «2→3→4 серии»).

## Рабочие договорённости (дайджест; полностью — CLAUDE.md и память)

- **Переписывание с нуля, никаких чёрных ящиков**; цикл: разобрать →
  понять зачем → решить, верно ли → своё решение. Старый код в
  `/home/user/series-tracker` (main) — справочник.
- **Форма обсуждения**: сначала «как сейчас» (нейтрально), затем
  развёрнутое предложение, вопрос прозой. Краткие опросники — нет.
- Баги старой системы чинятся по согласованию, фиксируются в revision.md.
- **Прод неприкасаем** (не подключаться, не записывать трафик).
- Уважение к внешним сервисам: паузы, rate-limit, персистентные сессии.
- БД — приватная память модуля (Р-7), межмодульное — только шина.
- Коммиты на русском, **без какой-либо ИИ-атрибуции**.

## Технические якоря

- Worktree: `~/series-tracker-bus` (ветка refactoring/bus), venv `.venv/`.
- Стенд: `ssh series-tracker` (user) / root; qBit в Docker :8080
  admin/REMOVED-SECRET; голая Ubuntu 24.04 + git/ffmpeg/yt-dlp/Docker.
- Констрейнты данных: формулы unique_id/torrent_id (Р-10), форматы
  дат-маркеров по трекерам (находка 13, modules/sources/dates.py),
  схема app.db = Alembic 0001.
- Колонка auth называется `auth_type` (не service); settings: key/value.
- Версия прод-qBittorrent неизвестна (находка 5) — клиент уже
  двупоколенный (204/200, 409/Fails., stop/pause).
