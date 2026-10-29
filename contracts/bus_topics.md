# Карта топиков шины (снимок после этапа 4, 2026-06-12)

> Сводный справочник для этапа 5: что уже есть на шине и кто владелец.
> Детали payload'ов — в докстрингах модулей и contracts/revision.md
> (Р-11..Р-17). Сгенерировано из кода (handle/publish_event), сгруппировано
> вручную.

## Queries / commands по модулям

| Модуль | Топики |
|---|---|
| catalog | series.list, series.get, status.get, series.create/update/delete (Р-19), series.set_save_path, series.touch_scan_time, viewing.start/stop |
| scan | series.run {series_id, force_replace?}, series.prepare {series_id} (состав торрент-раздачи без запуска закачки), all.start → {started}, status.get, availability {series_id} (последний отчёт о плеерах web-серии), composition {series_id, refresh?} (Р-21), media.list, media.downloaded_counts, item.set_ignored (+ scan.plan.updated), items.set_ignored_bulk {series_id, season, is_ignored} (+ scan.plan.updated) |
| sources | provider.info {url} → {provider, voices} (без сети), parse {url} (+torrent_id из core/ids, отчёт `players`), torrent_file.get/drop, trackers.list, tracker.resolve {url}, tracker.set_mirrors (Р-22), vk.scan, video.scan {url, series}, video.stream {url} → {handled, stream_url, headers: Referer/Origin/User-Agent доходят до ffmpeg целиком, Р-38} либо {handled, error, transient} (Р-25; для Vibix — `file://` на расшифрованный манифест; `error` — ссылка страницы серии без потока, загрузчик не отдаёт её yt-dlp) |
| rules | apply, test (Р-22), profiles.list/create/update/delete, rules.list/add/update/delete/reorder (Р-22), cache.invalidate, format_filename, format_torrent_file |
| torrents | add, info.get, files.get, pause/resume/recheck/delete, rename_file, set_location, db.active, db.deactivate_all, db.files.list/upsert, db.files.for_series, db.history, db.add, db.downloaded_counts, db.progress.list (Р-23), composition (Р-21), register {…, start_pipeline?} (false — подготовка состава без конвейера), queue.get, fs.verify |
| downloads | queue.get, queue.clear, fs.sync, item.set_filename, item.set_status, episodes.get {series_id} → референс-серии плана, fetch {key, url, path, max_height?, series_id?} (поток ссылки в файл вне медиатеки, Р-40), fetch.cancel {series_id} |
| slicing | chapters.get/filtered/mark, task.create, verify, deep_adoption, files.list, files.drop_for_source (Р-21), file.set_path, queue.get |
| renaming | reprocess, process_torrent {…, dry_run?} (разбор состава без переименования), tasks.active, preview (Р-21) |
| library | directories.list, relocate, relocation.active |
| metadata | search, details, map.get/list/set (владелец series_tmdb_mappings, Р-19) |
| settings | value.get, value.set, values.by_prefix (Р-22) |
| trackerauth | fetch, credentials.get/set (Р-22, владелец auth) |
| voices | layout {sources: [{url, player, studio, kind, episodes, links?}], mode?, reference?} → раскладка озвучек сериала с ключом озвучки (Р-39); приоритет источников — настройка `voices.source_priority`; dispatch {series_id} (скан → озвучки серий во временный каталог, Р-40); dubs.get {series_id} → записи voice_dubs |
| conform | submit {ref, dubs, out_dir, cache_dir, label?, key?} → {job_id} \| {rejected} \| {unavailable, retry} (постановка серии, Р-41; `key` попадает в метку задачи); job.find {key} → {reachable, job_id}; cancel {job_id} → {done}; job.get {job_id} → итог задачи; status {} → {configured, daemon: {up, version, device, gpu, gpu_name, error}, tasks} — зеркало очереди выравнивания демона track-muxer (Р-39); слушает `gateway.sse.clients` |

## События (издатель → подписчики)

| Событие | Издатель | Подписчики сейчас | SSE (этап 5) |
|---|---|---|---|
| series.status.contribution | scan, downloads, torrents, slicing | catalog | — |
| series.status.changed {series_id, statuses, is_busy} | catalog | gateway | series_updated {id, statuses, is_busy} (Р-18) |
| series.busy.contribution | library, renaming | catalog | — |
| series.busy.changed {series_id, is_busy, statuses} | catalog | gateway | series_updated {id, statuses, is_busy} (Р-18) |
| series.added {…series} | catalog | gateway | series_added (полный объект, Р-19) |
| series.updated {series_id, …поля, statuses, is_busy} | catalog | gateway, torrents (память исключений → приоритеты файлов) | series_updated (дельта, Р-19) |
| series.deleted {series_id, delete_from_qb} | catalog | scan, torrents, downloads, slicing, renaming, library, metadata, voices (каскад), gateway | series_deleted {id} (Р-19) |
| series.purged {series_id, owner} | scan, torrents, downloads, slicing, renaming, library, metadata, voices | catalog (учёт отчётов о чистке) | — |
| scan.plan.updated {series_id} | scan | downloads | — |
| scan.status.changed | scan | — | scanner_status_update |
| sources.video.progress {series_id, current, total, rate, phase, active} | sources | gateway | video_scan_progress |
| torrents.queue.changed {count, tasks} | torrents | scan (count=0 → следующий скан) | agent_queue_update |
| torrents.progress.changed {tasks} | torrents | gateway | torrent_progress_update (Р-23, замена поллинга) |
| downloads.queue.changed {tasks, count} | downloads | — | download_queue_update |
| downloads.fetch.finished {key, ok, error, transient, path, series_id} | downloads | voices (ключи `dub:<id>`) | — |
| conform.submit {ref, dubs, out_dir, cache_dir, label, key} (запрос) → `SubmitReply` {outcome: accepted/rejected/unavailable/uncertain, job_id?, reason?, retry} | voices | conform | — |
| conform.job.get {job_id} (запрос) → {job_id, status, error, results}; `gone` только когда демон спрошен и не знает задачу, недоступный демон — ошибка запроса | voices | conform | — |
| conform.cancel {job_id} (запрос) → {done} | voices | conform | — |
| conform.job.find {key} (запрос) → {reachable, job_id} | voices | conform | — |
| conform.workspace {dub_folder?, reference_folder?} (запрос) → {out_dir, cache_dir, scratch} — раскладка папок демона; чистая, демон не опрашивается | voices | conform | — |
| voices.dub.changed {series_id, season, episode, voice_key, voice_label, status, state, hint, detail, epoch, rev} — `DubEvent` | voices | gateway | voice_dub_update (Р-43, Р-44) |
| voices.dubs.snapshot {epoch, rev, items} — `DubsSnapshot` (при gateway.sse.clients) | voices | gateway | voice_dubs_snapshot |
| downloads.item.renamed {series_id, unique_id, filename} | downloads | voices | — |
| downloads.fetch.started {key, series_id, path} | downloads | voices (ключи `dub:<id>`) | — |
| voices.episode.settled {series_id, season, episode} | voices | — | — |
| conform.job.finished {job_id, status, error, results} | conform | voices | — |
| conform.daemon.up {} | conform | voices | — |
| slicing.queue.changed {tasks} | slicing | — | slicing_queue_update |
| conform.queue.changed {daemon, tasks} | conform | voices (состояние задач) | conform_queue_update (Р-39; вся картина на каждое изменение и при подключении клиента SSE) |
| gateway.sse.clients {count} | gateway | catalog, conform, voices | — |
| renaming.finished {series_id} | renaming | — | renaming_complete |
| library.relocation.started/finished | library | voices (finished) | relocation_started/finished |
| settings.changed {key, value} | settings | downloads (max_parallel_downloads), scan (настройки сканера → пересчёт расписания, Р-20) | — |
| gateway.sse.clients {count} | gateway | catalog (count=0 → сброс viewing), scan (count>0 → публикация статуса, Р-20) | — |
| trackerauth.credentials.changed {service} | trackerauth | torrents (qbittorrent → пересоздание клиента, Р-22) | — |

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
