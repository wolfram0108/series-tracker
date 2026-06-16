# Состояние пересоздания — точка продолжения

> Обновлено: 2026-06-12. Этот файл — снимок «где мы» для продолжения
> работы после сжатия контекста. Правила работы — в [CLAUDE.md](../CLAUDE.md)
> (целеполагание!), решения — в [contracts/revision.md](../contracts/revision.md)
> (Р-1..Р-23), находки — в [contracts/findings.md](../contracts/findings.md)
> (1–40), карта топиков шины — в
> [contracts/bus_topics.md](../contracts/bus_topics.md), план — в
> [docs/refactoring_bus_plan.md](refactoring_bus_plan.md).

## Выполнено (этапы 0–4 из 6)

| Этап | Состояние |
|---|---|
| 0. Инвентаризация | ✓ реестр 73 эндпоинтов (contracts/endpoints.md), golden GET-снимки и фикстура БД в tests/ (вне git), SSE-запись в ~/st-bus-stage0 (остановлена по требованию пользователя — прод больше не трогаем) |
| 1. Каркас | ✓ core/ (шина+конверт+BaseModule+раннер+логирование), gateway-скелет (FastAPI, SSE_MAP), run.py |
| 2. Инфраструктура | ✓ torrents (qBit-клиент: локальный infohash вкл. гибриды v2, два поколения API, релогин на 403), trackerauth (fetch-прокси, персистентные сессии, rate-limit), settings, metadata (TMDB), library (листинг), Alembic (0001 базовая схема 19 живых таблиц + 0002 tracker_sessions), core/db.py |
| 3. Мозги | ✓ rules (движок с нуля, фиксы А/Б/В), sources (Kinozal/RuTracker/Anilibria-API/VK-API; astar+anilibria_tv — разбор готов, браузерная доставка на этапе 6), scan/planner.py (SmartCollector v4.1 + фикс Г) |
| 4. Конвейер | ✓ статусная модель (Р-11): modules/catalog — агрегатор свёрток, series.status.changed только при изменениях, эфемерный viewing со страховкой gateway.sse.clients; находки 23–25. ✓ scan-оркестратор (Р-12): зеркала в sources, настоящая ресьюмабельность scan_tasks, формулы id верифицированы (190/190, 351/351), расписание автоскана, отказ параллельному скану; core: handle(concurrent=True); находки 26–29. ✓ downloads (Р-13): событийный диспетчер yt-dlp (async subprocess), ретрай ошибок сканом, fs.sync вместо 60-секундной ФС-проверки, агрегатор: waiting подавляется активностью; находки 30–32. ✓ торрент-конвейер (Р-14): ИНВАРИАНТ ЯДРА «пауза до конца переименования, magnet — запуск ровно на метаданные», чистая машина стадий (старые значения в БД), ошибки-носители stage='error', реализованы все контракты Р-12, адаптивный мониторинг прогресса, fs.verify. ✓ renaming + форматтер (Р-15): форматтер в rules с нуля, дифф имён 349/349, reprocess/process_torrent, событие renaming.finished, single_vk похоронен (находка 33); находки 33–34. ✓ slicing (Р-16): порт 1:1 (главы/фильтр/нарезка/verify/deep-adoption), ffmpeg+yt-dlp с таймаутами, ресьюмабельность progress_chapters, закрыты фейки Р-15; находка 35. ✓ library-relocation + is_busy (Р-17): перемещение VK-файлов/set_location, цепочка «переместили→переименовали», busy-вклады (только активная работа — находка 36 закрыта); **ЭТАП 4 ЗАВЕРШЁН** |

**Верификации против старого кода / реальных данных (все локально,
прод не участвует):** infohash 170/170 торрентов; движок правил
1088/1088 названий; планировщик 9/10 + 1 согласованное отклонение
(сериал 87, фикс Г); формулы id 190/190 (torrents) и 351/351
(media_items); имена файлов 349/349 (tests/test_rules_format_diff.py).
Тесты: 171 passed (после этапа 5) (`.venv/bin/python -m pytest -q`; изредка возможен
флак тестовых таймаутов под полной нагрузкой — код-гонок не выявлено),
интеграция со стендовым qBit — переменные `ST_QBIT_URL/USER/PASS` из
локального `.env` (см. `.env.example`), затем
`pytest tests/test_torrents_integration.py`.
Дифф-харнесы: `tests/verify_rules_diff.py`, `tests/verify_planner_diff.py`.

## СЛЕДУЮЩИЙ ШАГ (точка продолжения)

**Этап 5 (gateway) завершён** — ревизия 78 метод-точек старого
контракта (contracts/endpoints.md) шестью согласованными блоками.
**Следующий — этап 6 (стенд).** Блоки этапа 5:

1. ✓ **SSE-контракт (Р-18, sse_contract.md)**: SSE_MAP с трансформациями
   реализован; series_updated — дельта {id, statuses, is_busy} (catalog
   шлёт оба поля в обоих событиях — находка 38); очереди — голые
   массивы; agent_heartbeat удалён по согласованию (индикаторы остаются
   на queue/status-событиях); второе SSE-соединение (находка 37) —
   чинится в блоке 6.
2. ✓ **Серии (Р-19)**: карточка — композиция gateway (catalog + metadata
   + батч-счётчики, N+1 устранён); CRUD в catalog (series.added/updated/
   deleted → SSE); каскад удаления — событие series.deleted, владельцы
   чистят своё (enforce_fk=False для строки series); сохранение свойств —
   сценарий gateway (catalog → library.relocate | renaming.reprocess);
   /state — транспорт viewing.start/stop (без правки JS);
   viewing_heartbeat удалён; находка 39. composition и rename_preview
   перенесены в блок 4. modules/gateway/api_series.py; 10 тестов.
3. ✓ **Скан и очереди (Р-20)**: scanner/status сохранён (scan.status.get
   + публикация статуса при подключении SSE-клиента); настройки —
   пересчёт расписания вместо немедленного скана; scan_all/series-scan
   c 409; очереди agent/downloads; /api/agent/reset удалён (находка 23);
   находка 40 (поллинг вкладки «Агенты» — фикс в блоке 6).
   modules/gateway/api_system.py; 6 тестов.
4. ✓ **Media-items и операции серии (Р-21)**: главы/нарезка — на
   контракты Р-16; ignore → scan.plan.updated (вместо sync_vk_statuses);
   композиция по владельцам (scan.composition / torrents.composition);
   rename_preview → renaming.preview (dry-run); reprocess-точки — 409 по
   tasks.active + фоновая команда; удалены 3 мёртвые точки (int-ignore,
   reset_torrents, relocate). modules/gateway/api_media.py; 9 тестов.
5. ✓ **Настройки и справочники (Р-22)**: auth — trackerauth (+перелогин
   torrents по credentials.changed, env-приоритет для стенда); формулы
   id → core/ids.py; CRUD конструктора правил в rules + rules.test;
   debug_flags — группы новой системы + DEBUG-фильтр core/logging;
   tmdb/trackers/directories/logs; админ-вкладка БД (исключены auth,
   tracker_sessions). Удалены: database/clear, hello-страницы,
   directory-picker-test. **ВСЕ 78 МЕТОД-ТОЧЕК ОТРЕВИЗОВАНЫ.**
   modules/gateway/api_settings.py; 9 тестов.
6. ✓ **JS-слой (Р-23)**: viewing-setInterval и agent_heartbeat-слушатель
   удалены; settingsDebug на главном SSE (проп scannerStatus); вкладка
   «Агенты» — push torrent_progress_update вместо 5-секундного поллинга
   (+ исправлен мой маппинг active_torrents из Р-19: источник —
   прогресс download_tasks); /state запускает fs.sync/fs.verify;
   loadInitialSeries оставлены осознанно. Смоук под uvicorn пройден
   (заодно пойман и исправлен краш старта при пустых кредах qBit).

**ЭТАП 5 ЗАВЕРШЁН** (Р-18..Р-23): 78/78 точек отревизованы, SSE-контракт
закрыт, JS-слой переведён на новый контракт.

Сохранение свойств серии → catalog-обновление + library.relocate +
renaming.reprocess (блоки 2/4). Находка 7г: финальная сверка журнала
WORKER TIMEOUT с логами приложения — на этапе 6 (стенд).

После этапа 5 — этап 6 (стенд): учётки из
/home/user/series-tracker/docs/key.txt (ещё НЕ читал); Playwright для
astar/anilibria_tv; авторизованный дамп RuTracker; эмуляция сценариев
«2→3→4 серии»; живые проверки конвейера; находка 7г (сверка журнала
WORKER TIMEOUT с логами).

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
  (логин/пароль — в локальном `.env`); Ubuntu 24.04 + git/ffmpeg/yt-dlp/Docker.
- Констрейнты данных: формулы unique_id/torrent_id (Р-10), форматы
  дат-маркеров по трекерам (находка 13, modules/sources/dates.py),
  схема app.db = Alembic 0001.
- Колонка auth называется `auth_type` (не service); settings: key/value.
- Версия прод-qBittorrent неизвестна (находка 5) — клиент уже
  двупоколенный (204/200, 409/Fails., stop/pause).
