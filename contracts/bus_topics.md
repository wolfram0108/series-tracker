# Карта топиков шины (снимок после этапа 4, 2026-06-12)

> Сводный справочник для этапа 5: что уже есть на шине и кто владелец.
> Детали payload'ов — в докстрингах модулей и contracts/revision.md
> (Р-11..Р-17). Сгенерировано из кода (handle/publish_event), сгруппировано
> вручную.

## Queries / commands по модулям

| Модуль | Топики |
|---|---|
| catalog | series.list, series.get, status.get, series.set_save_path, series.touch_scan_time, viewing.start/stop |
| scan | series.run {series_id, force_replace?}, all.start, media.list, item.set_ignored |
| sources | parse {url}, torrent_file.get, trackers.list, vk.scan |
| rules | apply, profiles.list, cache.invalidate, format_filename, format_torrent_file |
| torrents | add, info.get, files.get, pause/resume/recheck/delete, rename_file, set_location, db.active, db.deactivate_all, db.files.list/upsert, register, queue.get, fs.verify |
| downloads | queue.get, queue.clear, fs.sync, item.set_filename, item.set_status |
| slicing | chapters.get/filtered/mark, task.create, verify, deep_adoption, files.list, file.set_path, queue.get |
| renaming | reprocess, process_torrent, tasks.active |
| library | directories.list, relocate, relocation.active |
| metadata | search, details |
| settings | value.get, value.set |
| trackerauth | fetch |

## События (издатель → подписчики)

| Событие | Издатель | Подписчики сейчас | SSE (этап 5) |
|---|---|---|---|
| series.status.contribution | scan, downloads, torrents, slicing | catalog | — |
| series.status.changed {series_id, statuses} | catalog | — | series_updated (оживляет ветку s.statuses) |
| series.busy.contribution | library, renaming | catalog | — |
| series.busy.changed {series_id, is_busy} | catalog | — | в series_updated (решить формат) |
| scan.plan.updated {series_id} | scan | downloads | — |
| scan.status.changed | scan | — | scanner_status_update |
| torrents.queue.changed {count, tasks} | torrents | scan (count=0 → следующий скан) | agent_queue_update |
| downloads.queue.changed {tasks, count} | downloads | — | download_queue_update |
| slicing.queue.changed {tasks} | slicing | — | slicing_queue_update |
| renaming.finished {series_id} | renaming | — | renaming_complete |
| library.relocation.started/finished | library | — | relocation_started/finished |
| settings.changed {key, value} | settings | downloads (max_parallel_downloads) | — |
| gateway.sse.clients {count} | gateway | catalog (count=0 → сброс viewing) | — |

## SSE старого контракта, требующие решения на этапе 5

- `series_added` / `series_deleted` — придут с CRUD сериалов (catalog,
  ревизия эндпоинтов).
- `agent_heartbeat` — пульсация индикаторов агентов; по принципу 2
  «heartbeat ради оживления UI — анти-паттерн», но индикаторы — часть
  дизайна. Решить с пользователем: выводить пульс из реальных событий
  (queue.changed и пр.) или воспроизвести.
- Вызовы при открытии модалки статуса: catalog.viewing.start +
  downloads.fs.sync + torrents.fs.verify (решения Р-11/Р-13/Р-14).
- Сохранение свойств сериала: catalog-обновление + library.relocate
  (при смене пути) + renaming.reprocess (Р-15/Р-17).
