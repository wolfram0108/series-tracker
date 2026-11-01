# Карта топиков шины (после этапа 10, П-8, 2026-11-01)

> Сводный справочник: что есть на шине и кто владелец. Детали payload'ов — в докстрингах `modules/*/module.py`
> (читать первыми) и в моделях `core/contracts`; решения — `contracts/revision.md` и
> `docs/missions/voice-alignment/stage-10/REVISION.md`. Перечень снят с кода (вызовы `handle` и `publish_event`),
> описания — из докстрингов модулей. Контракт шины: подписчик получает каждое подходящее сообщение один раз, в порядке
> публикации; сообщения не переживают перезапуск, поэтому обработчики идемпотентны, а продолжение работы берётся из БД.
> Каждый конверт несёт `correlation_id`.

## Запросы и команды по модулям

Запрос отвечает значением; команда ответа не ждёт. Отказ запроса — исключение владельца: текст `ValueError` — код
причины (`core/reasons`, формулировку даёт интерфейс), `LookupError` — «нет такой сущности».

| Модуль | Топики |
|---|---|
| catalog | `catalog.series.list` → `[{…сериал, kind, sources, statuses, is_busy}]`; `catalog.series.get {series_id}` → то же для одного; `catalog.series.create {data}` → сериал (`data.sources` — минимум один источник; отказ `no_source`, `source_exists`; поля `url`, `source_type`, `parser_profile_id`, `vk_search_mode`, `vk_quality_priority` у сериала удалены — отказ с именем поля) + `series.added`, затем `series.source.added` на каждый источник; `catalog.series.update {series_id, fields}` → `{applied}` (источники здесь не меняются) + `series.updated`; `catalog.series.delete {series_id, delete_from_qb?}` → `{ok}` + `series.deleted` (каскад по событию); `catalog.series.set_save_path {series_id, save_path}` (пишет library после переноса); `catalog.series.touch_scan_time`; `catalog.viewing.start/stop {series_id}` (команды); **источники сериала (Д-38):** `catalog.sources.list {series_id}` → `[источник]` по `position`; `catalog.sources.by_profile {profile_id}` → источники всех сериалов; `catalog.sources.add {series_id, source}` → `{id}`; `catalog.sources.update {source_id, fields, series_id?}` → `{applied}` (с `series_id` источник чужого сериала — `LookupError`); `catalog.sources.remove {source_id, series_id?}` → `{ok}` (единственный источник — отказ `last_source`; с `series_id` источник чужого сериала — `LookupError`); источник — модель `SeriesSource`; вид сериала `kind` (`torrent` либо `video`) считает одна функция каталога |
| sources | `sources.table.get {url, options?, refresh?}` → `TableGetReply {url, state: ready \| reading \| failed \| absent, table?, reason?}` — таблица страницы сайта (З-1): полная таблица моложе часа отдаётся сразу, иначе страницу читает один процесс чтения на страницу, ответ `reading` сразу, итог — события `sources.table.*`; `sources.table.read {url, options?}` (команда: прочитать сейчас); `sources.site.resolve {url, options?}` → `{site, canonical_url}` — какой сайт читает адрес (без сети; оба `null`, если никакой); `sources.video.stream {url, max_height?}` → `{handled, stream_url, progressive, headers}` либо `{handled, error, transient}` — поток кандидата перед загрузкой (`progressive` — готовый файл по HTTP Range; для Vibix `stream_url` — `file://` на расшифрованный манифест); торрентная часть: `sources.parse {url}` → `{service, title, releases[], tracker, ui_features}` (только страницы трекеров; страницу сайта читает `sources.table.get`), `sources.torrent_file.get {url, torrent_id}` → `{content_b64}` (с кэшем), `sources.torrent_file.drop {torrent_id}`, `sources.trackers.list`, `sources.tracker.resolve {url}` → `{tracker \| null}`, `sources.tracker.set_mirrors {tracker_id, mirrors}` |
| rules | `rules.apply {profile_id, lines: [{key, text}], series_season?}` → `{results: {key: {excluded, season, episode, start, end, part, voiceover, events, errors}}, invalid_rules}` — единственный источник номеров (З-3), модели `RulesApplyRequest/Reply`; `rules.format_filename {series, media_item, episode_override?, original_filename?}` → `{filename}` — имя по готовым номерам, правила повторно не применяются; `rules.format_torrent_file {series, profile_id, file_basename, original_path}` → `{filename, extracted}`; `rules.profiles.list/create/update/delete`, `rules.rules.list/add/update/delete/reorder` (профиль, назначенный источникам, удалить нельзя — `profile_in_use`) |
| composition | `composition.get {series_id, include_excluded?}` → `CompositionReply` (строки раскладки в порядке источников, сводка по источникам, рычаги сериала и сезонов, итоговые рычаги каждой серии; исключённые конструктором строки — только по `include_excluded`); `composition.recompute {series_id}` → `{rev, changed}` (посчитать сейчас по хранимым таблицам и ответить, когда расчёт записан и событие вышло); `composition.preview {url, options?, profile_id?, series_season?, refresh?}` → `CompositionPreviewReply` — адрес, который ещё не источник сериала: состояние таблицы, сводка, строки для конструктора и ответ на каждую (ничего не пишет); `composition.settings.set {series_id, season?, episode?, lever, target?, value}` → `{ok}` (отказ `unknown_lever`, `bad_target`, `bad_value`); `composition.settings.reset {series_id, season?, episode?, lever?, target?}` → `{ok}` |
| scan | `scan.series.run {series_id, force_replace?, download?}` — торрент: проход по раздачам; видео: синхронизация источников (таблицы → раскладка → референсы → план → при `download` загрузки и озвучки), отказ «уже запущен» при скане той же серии; `scan.series.prepare {series_id}` (состав торрент-раздачи без закачки); `scan.output.get {series_id}` → `[Reference]` — референс каждой серии и отметка (`by_level`, `own`, `other_player`, `none`, `unfit`); `scan.all.start` → `{started}`; `scan.status.get`; `scan.media.list {series_id}`; `scan.media.downloaded_counts` → `{counts}`; `scan.seasons {series_id}` → `{seasons}`; `scan.item.set_ignored {unique_id, is_ignored}` и `scan.items.set_ignored_bulk {series_id, season, is_ignored}` (+ `scan.plan.updated`, `scan.reference.changed` у изменившихся серий) |
| voices | `voices.groups.resolve {names}` → `{groups: {имя: группа \| null}}` (только пользовательские группы, З-6); `voices.settings.get` → `VoiceSettingsView`; `voices.settings.set` `VoiceSettingsDraft` → `VoiceSettingsSaved` (проверка целиком, запись одной пачкой через `settings.values.set_many`, отказ — коды причин); `voices.dubs.get {series_id}` → `{items}` — озвучки по сериям с состоянием; `voices.dispatch {series_id}` (команда скана: план озвучек по `composition.get` и `scan.output.get`, допуск к загрузке) |
| downloads | `downloads.dispatch {series_id}` — единственная точка старта загрузки (шлёт только скан по явному скану); `downloads.queue.get` → `{tasks, count}`; `downloads.queue.clear` → `{deleted}`; `downloads.cancel {task_id}`; `downloads.fs.sync {series_id}` → `{adopted, lost}` (сверка с диском; сериал вида `torrent` — сразу `{0, 0}`, ничего не публикуется); `downloads.episodes.get {series_id}` → `{items}` — референс-серии плана; `downloads.item.set_filename`, `downloads.item.set_status`; `downloads.fetch {key, url, path, max_height?, series_id?}` — поток по ссылке в файл вне медиатеки (озвучки, Р-40), `downloads.fetch.cancel {series_id?, key?}`; поток кандидата разрешает только `sources.video.stream`, запасной путь yt-dlp — по плееру источника кандидата |
| renaming | `renaming.reprocess {series_id}` — массовая переобработка имён по `kind` сериала (видео — по готовым номерам строк, торрент — по профилю торрентного источника); `renaming.process_torrent {series_id, qb_hash, dry_run?}`; `renaming.tasks.active {series_id}`; `renaming.preview {series_id}` |
| library | `library.directories.list {path}`; `library.relocate {series_id, new_path}` (по завершении library сам вызывает `renaming.reprocess`) |
| metadata | `metadata.search`, `metadata.details {tmdb_id}`, `metadata.map.get/list/set` (владелец `series_tmdb_mappings`), `metadata.seasons.recompute {series_id}` (число серий многосезонного сериала → `series.updated`) |
| torrents | `torrents.add`, `torrents.info.get`, `torrents.files.get`, `torrents.pause`, `torrents.resume`, `torrents.recheck`, `torrents.delete`, `torrents.rename_file`, `torrents.set_location`; `torrents.db.active/deactivate_all/add/history/downloaded_counts/progress.list/files.list/files.upsert/files.for_series`; `torrents.register {…, start_pipeline?}` (false — подготовка состава без конвейера); `torrents.composition {series_id, refresh?}` (композиция торрента); `torrents.queue.get`; `torrents.fs.verify {series_id}` (сериал вида `video` — сразу `{0, 0}`); `torrents.drive_incomplete`; `torrents.seasons {series_id}`; `torrents.preview.fetch {items: [{torrent_id, magnet?, link?}]}` — списки файлов раздач формы добавления: magnet через qBit, link — .torrent из `sources.torrent_file.get`; исход каждой раздачи — событие `torrents.preview.ready` либо `torrents.preview.failed` (издаёт только torrents), ответа нет; адрес и профиль торрента берутся из источника сериала |
| slicing | `slicing.chapters.get/filtered/mark`, `slicing.task.create`, `slicing.verify`, `slicing.deep_adoption`, `slicing.files.list`, `slicing.files.drop_for_source`, `slicing.file.set_path`, `slicing.source.delete`, `slicing.queue.get`; в архитектуре этапа 10 модуль не участвует (Д-34): склейка в план не попадает |
| conform | `conform.submit {ref, dubs, out_dir, cache_dir, label?, key?}` → `SubmitReply` (`accepted`, `rejected`, `unavailable`, `uncertain`); `conform.job.find {key}` → `{reachable, job_id}`; `conform.job.get {job_id}` → итог задачи (`gone` — демон спрошен и не знает задачу); `conform.cancel {job_id}` → `{done}` (ждущую или идущую останавливает, запись завершённой удаляет); `conform.plot {job_id, name}` → `{found, data?}` (PNG, base64); `conform.workspace {dub_folder?}` → две папки внутри рабочей папки / раскладка папок сервиса; `conform.settings.get` → `ConformSettingsView`; `conform.settings.set` `ConformSettingsDraft` → `ConformSettingsSaved` |
| settings | `settings.value.get/set`, `settings.values.by_prefix`, `settings.values.set_many {values}` (одна транзакция, `settings.changed` на каждый ключ), `settings.paths.add/list/remove` |
| trackerauth | `trackerauth.fetch`, `trackerauth.credentials.get/set` |
| auth | `auth.exists`, `auth.login`, `auth.password.set` |

## События (издатель → подписчики)

| Событие | Издатель | Подписчики | SSE |
|---|---|---|---|
| `system.started {}` — все модули подняты | `run.py` (сборка процесса, после `Runner.start`) | gateway (читает группы отладочного логирования) | — |
| `gateway.sse.clients {count}` | gateway | catalog (0 → сброс viewing), scan (>0 → публикация статуса), conform и voices (снимок при подключении) | — |
| `series.added {…сериал, kind, sources}` | catalog | composition, gateway | `series_added` |
| `series.updated {series_id, …поля, statuses, is_busy}` | catalog, metadata | composition (сезон карточки), torrents (память исключений), gateway | `series_updated` (дельта) |
| `series.deleted {series_id, delete_from_qb}` | catalog | все владельцы серийных таблиц (scan, torrents, downloads, slicing, renaming, library, metadata, voices, composition), gateway | `series_deleted {id}` |
| `series.purged {series_id, owner}` | владельцы серийных таблиц | catalog (учёт отчётов о чистке) | — |
| `series.source.added \| .updated \| .removed {series_id, source_id}` — `SourceEvent` | catalog | composition, gateway | `series_sources_changed` (`SourceChanged`: плюс `what`) |
| `series.status.contribution {source, series_id, flags}` | scan, downloads, torrents, slicing | catalog | — |
| `series.status.changed {series_id, statuses, is_busy}` | catalog | gateway | `series_updated` |
| `series.busy.contribution` / `series.busy.changed` | library, renaming / catalog | catalog / gateway | `series_updated` |
| `series.downloaded.changed {series_id, count}` | downloads, torrents | voices, gateway | `series_updated` (счётчик скачанного) |
| `sources.table.progress {url, phase, current, total}` | sources | gateway | `sources_table_progress` |
| `sources.table.ready {url, read_at}` — полная таблица сохранена | sources | composition, scan, gateway | `sources_table_ready` |
| `sources.table.failed {url, reason}` — чтение не удалось, действует прежняя полная таблица | sources | composition, scan, gateway | `sources_table_failed` |
| `rules.profile.changed {profile_id}` | rules | composition | — |
| `composition.changed {series_id, rev, episodes, summary_only}` — `CompositionChanged`; `episodes` пусто — весь сериал; `summary_only` — изменились только сводки источников | composition | scan, voices, gateway | `composition_changed` |
| `composition.failed {series_id, reason}` — пересчёт не удался, действует прежняя раскладка | composition | gateway | `composition_failed` |
| `scan.reference.changed` — `ReferenceChanged`: референс серии целиком (поля `Reference`: `season`, `episode`, `part`, `mark`, `source_id`, `row_key`, `player`, `voice_group`, `height`, `reason`) и `series_id` | scan | voices, gateway | `reference_changed` |
| `scan.plan.updated {series_id}` — план пересчитан (загрузку не запускает) | scan | downloads | — |
| `scan.status.changed` | scan | gateway | `scanner_status_update` |
| `settings.changed {key, value}` | settings | downloads, scan | — |
| `trackerauth.credentials.changed {service}` | trackerauth | torrents | — |
| `torrents.queue.changed {count, tasks}` | torrents | scan (0 → следующий проход), gateway | `agent_queue_update` |
| `torrents.progress.changed {tasks}` | torrents | gateway | `torrent_progress_update` |
| `torrents.preview.ready \| .failed` — исход одной раздачи команды `torrents.preview.fetch` | torrents | gateway | `preview_naming_ready`, `preview_naming_failed` |
| `downloads.queue.changed {tasks, count}` | downloads | gateway | `download_queue_update` |
| `downloads.fetch.started \| .finished {key, …}` | downloads | voices | — |
| `downloads.item.renamed {series_id, unique_id, filename}` | downloads | voices | — |
| `slicing.queue.changed {tasks}` | slicing | gateway | `slicing_queue_update` |
| `renaming.finished {series_id}` | renaming | gateway | `renaming_complete` |
| `library.relocation.started \| .finished` | library | voices (`finished`), gateway | `relocation_started`, `relocation_finished` |
| `voices.dub.changed` — `DubEvent` | voices | gateway | `voice_dub_update` |
| `voices.dubs.snapshot {epoch, rev, items}` — `DubsSnapshot` (при `gateway.sse.clients`) | voices | gateway | `voice_dubs_snapshot` |
| `voices.settings.changed` — `VoiceSettingsView` | voices | composition, scan, gateway | `voice_settings_update` |
| `voices.episode.settled {series_id, season, episode}` | voices | — | — |
| `conform.queue.changed {daemon, tasks}` | conform | voices, gateway | `conform_queue_update` |
| `conform.settings.changed` — `ConformSettingsView` | conform | voices, gateway | `conform_settings_update` |
| `conform.job.finished {job_id, status, error, results}` / `conform.job.removed {job_id}` / `conform.daemon.up {}` | conform | voices | — |

## SSE

`SSE_MAP` шлюза (`modules/gateway/module.py`) — единственная таблица «топик шины → имя события интерфейса»; пакеты
событий самодостаточны для своего участка экрана, интерфейс ничего не доспрашивает. Контракт по каждому событию —
`contracts/sse_contract.md`. Модели пакетов этапа 10 (`TableProgress`, `TableReady`, `TableFailed`, `CompositionChanged`,
`CompositionFailed`, `ReferenceChanged`) шлюз кладёт в схему API рядом с маршрутами: типы интерфейса строятся из них.

## Что ушло в этапе 10

Топики, которых в коде больше нет (`REVISION.md`): `sources.video.scan`, `sources.video.layout`, `sources.video.coverage`,
`sources.vk.scan`, `sources.provider.info`, а у `sources.parse` — ветка веб-источников; события
`sources.video.progress` и `sources.coverage.progress` (SSE `video_scan_progress`, `coverage_progress`);
`scan.composition`, `scan.availability` (и таблица `video_availability`); `voices.layout`; `rules.test`,
`rules.cache.invalidate`.
