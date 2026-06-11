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
| 4. Конвейер (идёт) | ✓ статусная модель (Р-11): modules/catalog — агрегатор свёрток, series.status.changed только при изменениях, эфемерный viewing со страховкой gateway.sse.clients; находки 23–25. ✓ scan-оркестратор (Р-12): зеркала в sources, настоящая ресьюмабельность scan_tasks, формулы id верифицированы (190/190, 351/351), расписание автоскана, отказ параллельному скану; core: handle(concurrent=True); находки 26–29 |

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

Дальше по этапу 4 (каждый модуль — с разбора пользователю):
downloads (yt-dlp; VK-свёртка downloading/error/ready/waiting —
семантика sync_vk_statuses: ready из ВСЕХ непроигнорированных,
waiting=есть pending в плане и нет активности, сосуществует с ready;
обработчик scan.plan.updated), торрент-конвейер (стадии agent_tasks →
metadata/renaming/checking/activating; мониторинг download_tasks →
downloading/ready; torrents.db.*/register/queue.*), slicing (ffmpeg +
utils/chapter_*), renaming (reprocess + logic/renaming_processor +
filename_formatter — форматтер ещё НЕ перенесён), library-relocation
(+is_busy — пока вне статусной модели, решить при разборе). Находка
7г: при разборе агентов выяснить, что тикает с периодом ~1,5 часа
(статусная зона и scan кандидата не дали: такты 5 с/60 с, автоскан в
проде — 360 мин).

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
