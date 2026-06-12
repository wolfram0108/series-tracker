# Карта топиков шины (снимок после этапа 4, 2026-06-12)

> Сводный справочник для этапа 5: что уже есть на шине и кто владелец.
> Детали payload'ов — в докстрингах модулей и contracts/revision.md
> (Р-11..Р-17). Сгенерировано из кода (handle/publish_event), сгруппировано
> вручную.

## Queries / commands по модулям

| Модуль | Топики |
|---|---|
| catalog | series.list, series.get, status.get, series.create/update/delete (Р-19), series.set_save_path, series.touch_scan_time, viewing.start/stop |
| scan | series.run {series_id, force_replace?}, all.start → {started}, status.get, composition {series_id, refresh?} (Р-21), media.list, media.downloaded_counts, item.set_ignored (+ scan.plan.updated) |
| sources | parse {url}, torrent_file.get, torrent_file.drop, trackers.list, tracker.resolve {url}, vk.scan |
| rules | apply, profiles.list, cache.invalidate, format_filename, format_torrent_file |
| torrents | add, info.get, files.get, pause/resume/recheck/delete, rename_file, set_location, db.active, db.deactivate_all, db.files.list/upsert, db.files.for_series, db.history, db.add, db.downloaded_counts, composition (Р-21), register, queue.get, fs.verify |
| downloads | queue.get, queue.clear, fs.sync, item.set_filename, item.set_status |
| slicing | chapters.get/filtered/mark, task.create, verify, deep_adoption, files.list, files.drop_for_source (Р-21), file.set_path, queue.get |
| renaming | reprocess, process_torrent, tasks.active, preview (Р-21) |
| library | directories.list, relocate, relocation.active |
| metadata | search, details, map.get/list/set (владелец series_tmdb_mappings, Р-19) |
| settings | value.get, value.set |
| trackerauth | fetch |

## События (издатель → подписчики)

| Событие | Издатель | Подписчики сейчас | SSE (этап 5) |
|---|---|---|---|
| series.status.contribution | scan, downloads, torrents, slicing | catalog | — |
| series.status.changed {series_id, statuses, is_busy} | catalog | gateway | series_updated {id, statuses, is_busy} (Р-18) |
| series.busy.contribution | library, renaming | catalog | — |
| series.busy.changed {series_id, is_busy, statuses} | catalog | gateway | series_updated {id, statuses, is_busy} (Р-18) |
| series.added {…series} | catalog | gateway | series_added (полный объект, Р-19) |
| series.updated {series_id, …поля, statuses, is_busy} | catalog | gateway | series_updated (дельта, Р-19) |
| series.deleted {series_id, delete_from_qb} | catalog | scan, torrents, downloads, slicing, renaming, library, metadata (каскад), gateway | series_deleted {id} (Р-19) |
| scan.plan.updated {series_id} | scan | downloads | — |
| scan.status.changed | scan | — | scanner_status_update |
| torrents.queue.changed {count, tasks} | torrents | scan (count=0 → следующий скан) | agent_queue_update |
| downloads.queue.changed {tasks, count} | downloads | — | download_queue_update |
| slicing.queue.changed {tasks} | slicing | — | slicing_queue_update |
| renaming.finished {series_id} | renaming | — | renaming_complete |
| library.relocation.started/finished | library | — | relocation_started/finished |
| settings.changed {key, value} | settings | downloads (max_parallel_downloads), scan (настройки сканера → пересчёт расписания, Р-20) | — |
| gateway.sse.clients {count} | gateway | catalog (count=0 → сброс viewing), scan (count>0 → публикация статуса, Р-20) | — |

## SSE: контракт закрыт Р-18 (см. sse_contract.md)

SSE_MAP gateway реализован (блок 1 этапа 5): series_updated — дельта,
очереди — голые массивы, agent_heartbeat — удалён по согласованию,
второе SSE-соединение фронта (находка 37) чинится в блоке 6.

Блок 2 (Р-19) закрыл: series_added/series_deleted (CRUD в catalog),
сохранение свойств (сценарий gateway: catalog → library/relocate |
renaming.reprocess), POST /state как транспорт viewing.start/stop.

Остаётся на следующие блоки:
- Вызовы при открытии модалки статуса: catalog.viewing.start (уже
  работает через /state) + downloads.fs.sync + torrents.fs.verify
  (Р-13/Р-14, блок 4/6).
