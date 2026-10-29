# SSE-контракт (Р-18, этап 5, блок 1)

> Полный контракт push-обновлений фронта. Старый контракт снят с кода
> прода (sse.py + static/js/app.js + agents/*), решения согласованы
> 2026-06-12 (Р-18 в [revision.md](revision.md)). Реализация —
> `modules/gateway/module.py` (SSE_MAP: топик шины → имя SSE +
> трансформация payload).

## Механика

- Один HTTP-эндпоинт `GET /api/stream`; keepalive-комментарий каждые 15 с.
- Gateway — адаптер протоколов: событие шины → SSE по таблице SSE_MAP;
  трансформация — только перекладка полей под форму, которую ждёт фронт,
  без бизнес-логики.
- Старый брокер (queue maxsize=5, молчаливое удаление клиента при
  переполнении) не воспроизводится: у gateway очередь подписчика шины,
  потеря событий допустима только в рамках принципа 3 (состояние
  восстанавливается из БД при следующем событии/загрузке).

## События: старый контракт → решение

| SSE-событие | Старый payload | Решение | Топик шины | Payload SSE (новый) |
|---|---|---|---|---|
| `series_updated` | полный объект серии | **перепроектировано: дельта** | `series.status.changed`, `series.busy.changed` | `{id, statuses, is_busy}` |
| `series_added` | полный объект серии | подтверждено; добавится при ревизии CRUD (блок 2) | — (будет catalog) | полный объект серии |
| `series_deleted` | `{id}` | подтверждено; блок 2 | — (будет catalog) | `{id}` |
| `agent_queue_update` | голый массив задач | подтверждено | `torrents.queue.changed {count, tasks}` | голый массив `tasks` |
| `download_queue_update` | голый массив задач | подтверждено | `downloads.queue.changed {tasks, count}` | голый массив `tasks` |
| `slicing_queue_update` | голый массив задач | подтверждено | `slicing.queue.changed {tasks}` | голый массив `tasks` |
| `conform_queue_update` | — (новое, Р-39) | зеркало очереди выравнивания демона; модуль `conform` шлёт всю картину при каждом изменении и при подключении клиента SSE (по `gateway.sse.clients`), поэтому открытый экран не доспрашивает состояние | `conform.queue.changed {daemon, tasks}` | как есть: `{daemon: {up, error?, version?, device?, gpu_name?}, tasks: [{id, label, status, progress, stage, stage_pct, dub_index, dub_total, cur_dub, elapsed_s, error, results: [{dub, ok, skipped, resid_ms, coverage, cuts, suspect, warnings, critical, error}]}]}` |
| `voice_dub_update` | — (новое, Р-43) | состояние одной дополнительной озвучки серии; пакет самодостаточен: интерфейс заменяет запись озвучки в серии и ничего не доспрашивает, из двух пакетов оставляет с большей парой (`epoch`, `rev`); `removed` убирает запись (и не даёт запоздавшему пакету вернуть её), `stale` начинает озвучку заново, `moved` не меняет состояние; о каждой записи строки сообщается, включая создание запланированной строки и исключение из плана | `voices.dub.changed` | как есть: `{series_id, season, episode, voice_key, voice_label, status, state, hint, detail, epoch, rev}` — модель `DubEvent` из `core/contracts`; событие показывает строку такой, какая она в момент объявления, поэтому шаги, следующие друг за другом сразу, могут быть показаны последним из них; `state` — что видит пользователь (Р-44): `planned`, `download_queued`, `downloading`, `align_queued`, `running`, `done`, `error`; `hint` — какая из подсказок `voices.hint.*` объясняет состояние (`planned`, `download_queued`, `downloading`, `ready`, `awaiting_answer` (отправлено в сервис выравнивания, номера задачи ещё нет), `aligning_wait`, `aligning_run`, `aligned`, `done`; `null` у ошибки — показывается причина); `status` — шаг за ним: `pending`, `downloading`, `ready`, `aligning`, `aligned`, `placed`, `rejected`, `failed`, `moved`, `stale`, `removed`; `detail` — причина отказа, ошибки или устаревания `{code, params}` (слова собирает интерфейс по ключам `reason.*`, `external` — слова чужой системы в `params.text`), иначе `null`; `epoch` — номер запуска модуля, хранится в БД и растёт при каждом запуске; `rev` — номер пакета внутри запуска; часы в порядке пакетов не участвуют |
| `voice_dubs_snapshot` | — (новое) | все озвучки всех сериалов; модуль `voices` шлёт при подключении клиента SSE (по `gateway.sse.clients`), поэтому открытый экран не доспрашивает состояние; интерфейс заменяет известное снимком, кроме записей, у которых пара (`epoch`, `rev`) больше; последний принятый снимок задаёт границу: пакеты и снимки, не новее его, отвергаются, в том числе пакет существования, запоздавший после удаления записи и более нового пустого снимка | `voices.dubs.snapshot` | как есть: `{epoch, rev, items: [{series_id, season, episode, voice_key, voice_label, status, state, hint, detail}]}` — модель `DubsSnapshot`; `rev` взят до чтения БД, поэтому событие с большей парой (`epoch`, `rev`) новее снимка; снимок более позднего запуска заменяет всё известное |
| `voice_settings_update` | — (новое, Р-54) | настройки озвучек после сохранения (`VoiceSettingsView`); пакет самодостаточен: открытые экраны настроек применяют его без запроса | `voices.settings.changed` | как есть |
| `scanner_status_update` | `{scanner_enabled, scan_interval, is_scanning, is_awaiting_tasks, next_scan_time}` | подтверждено | `scan.status.changed` | как есть (форма совпадает) |
| `video_scan_progress` | — (новое) | прогресс опроса серий web-источника | `sources.video.progress {series_id, current, total, rate, phase, active}` | как есть |
| `agent_heartbeat` | `{name, activity?}` | **удалено по согласованию** (см. ниже) | — | — |
| `relocation_started` | `{series_id}` | подтверждено | `library.relocation.started` | как есть |
| `relocation_finished` | `{series_id, success, message}` | подтверждено | `library.relocation.finished` | как есть |
| `renaming_complete` | `{series_id}` | подтверждено | `renaming.finished` | как есть |
| `torrent_progress_update` | — (новое, Р-23) | замена поллинга вкладки «Агенты» (находка 40) | `torrents.progress.changed {tasks}` | голый массив `tasks` |

## Решения и обоснования

### series_updated — дельта вместо полного объекта

Принцип 2 («целимся в дельту»). Обработчик фронта (`app.js:271`) делает
`Object.assign` — слияние дельты работает без правок JS; мёртвая ветка
`s.statuses` (находка 24) оживает. **Констрейнт (находка 38):** каждый
`series_updated` обязан нести актуальный `is_busy` — фронт снимает
спиннер сохранения (`savingSeriesIds`) по falsy `is_busy`. Поэтому
catalog включает оба поля (`statuses`, `is_busy`) и в
`series.status.changed`, и в `series.busy.changed`.

### agent_heartbeat — удалён

Согласовано пользователем 2026-06-12: вспышки «я жив» — легаси-функция
эпохи нестабильного прода («чтобы было видно, что всё хорошо»). В старом
коде семантика была неоднородна: monitoring мигал при реально выполненной
периодической работе, downloader/slicing — безусловно каждый тик цикла.
Индикаторы агентов в шапке **остаются** — ими управляют
`*_queue_update` (синий/пульс при непустой очереди) и
`scanner_status_update` (активность сканера). Удаляется только
вспышка-пульс. JS-слушатель `agent_heartbeat` (app.js:236) удаляется в
пакете правок блока 6.

### Реализация блока 6 (Р-23)

Все решения внесены: слушатель agent_heartbeat и viewing-setInterval
удалены из app.js; settingsDebug.js получает scannerStatus пропсом с
главного соединения; вкладка «Агенты» питается torrent_progress_update.
Осознанное наследие: loadInitialSeries() в relocation_finished /
renaming_complete (одиночная реакция, не поллинг).

### Второе SSE-соединение — устранено (блок 6)

Находка 37: `settingsDebug.js:335` открывает второй `EventSource` ради
одного `scanner_status_update`. Согласовано: починить правильно —
перевести на главное соединение app.js (правка JS в блоке 6); gateway
при этом многоклиентский и второе соединение технически переживёт.

## Служебные события шины (не SSE)

`gateway.sse.clients {count}` — счётчик подключений; при 0 catalog
сбрасывает эфемерные viewing (Р-11).
