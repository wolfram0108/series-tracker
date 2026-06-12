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
| `scanner_status_update` | `{scanner_enabled, scan_interval, is_scanning, is_awaiting_tasks, next_scan_time}` | подтверждено | `scan.status.changed` | как есть (форма совпадает) |
| `agent_heartbeat` | `{name, activity?}` | **удалено по согласованию** (см. ниже) | — | — |
| `relocation_started` | `{series_id}` | подтверждено | `library.relocation.started` | как есть |
| `relocation_finished` | `{series_id, success, message}` | подтверждено | `library.relocation.finished` | как есть |
| `renaming_complete` | `{series_id}` | подтверждено | `renaming.finished` | как есть |

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

### Второе SSE-соединение — устраняется (блок 6)

Находка 37: `settingsDebug.js:335` открывает второй `EventSource` ради
одного `scanner_status_update`. Согласовано: починить правильно —
перевести на главное соединение app.js (правка JS в блоке 6); gateway
при этом многоклиентский и второе соединение технически переживёт.

## Служебные события шины (не SSE)

`gateway.sse.clients {count}` — счётчик подключений; при 0 catalog
сбрасывает эфемерные viewing (Р-11).
