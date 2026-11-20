# Опись топиков шины — этап 0 миссии `modular-architecture`, основание О-4

Состояние на 20.11.2026, дерево `e3480f58` (`main`). Рабочий документ миссии: меняется вместе с реестром этапа 1.

## Как составлена

- Все места `core/` и `modules/`, где топик назван: `self.handle`, `request` и его обёртки (`_ask`, `asked`, `told`, `_step`,
  `_exported`, `_counts`), `send_command`, `publish_event` и обёртки событий (`_publish`, `publish`), с разбором констант и
  f-строк (AST). Найдено 571 место, 231 строка топика; обработчиков (`handle`) 222.
  Топики, которые назвать нельзя без чтения (топик из переменной): три цикла подписок (sources, unpack, planner), `job()` планировщика
  (`conform.submit`, `conform.reverse`), `set_state` шлюза (`catalog.viewing.start|stop`), `{owner}.work.get` скана — разобраны вручную.
- Вид по сути определён по коду обработчика: прослежены `self.*`, методы репозитория (SQL), вспомогательные объекты. Выводы по
  модулям сверены с таблицей, посчитанной по графу вызовов (AST), и выборочно перечитаны по коду (`sources.table.get`,
  `unpack.editor.get`, `planner.work.get`, `sources.provider.get`, `catalog.sources.add`). Топики, где чтение модуля дало «смешанный»
  для ветки-отказа или идемпотентного повтора (`torrents.release.download`, `torrents.fs.verify`, `torrents.drive_incomplete`,
  `torrents.preview.fetch`), приведены к правилу ниже: они command.
- Правило. **query** — ничего не меняет: нет записи в базу, нет действия во внешнем мире (файлы, qBittorrent, служба выравнивания,
  сеть на запись), нет публикации об изменении, нет команды соседу, нет изменения состояния модуля в памяти, которое читают другие
  вызовы. Кэш, который заполняет сам запрос, и чтение из сети состоянием не считаются. **command** — меняет состояние (безусловно или
  при условии, которое проверяет сам обработчик: отказ и «уже сделано» остаются командой). **mixed** — один и тот же топик по
  параметру вызова или по состоянию (ленивая инициализация, пересчёт устаревшего) то только читает, то меняет. **event** — факт,
  который публикует модуль; реакция подписчика — дело подписчика.
- Пути `module.py:NN` — в каталоге модуля-владельца (`modules/<владелец>/`). Номера строк — по ветке прототипа О-1 (после переноса файлов в `calc` и `modules/planner`): в `modules/planner/module.py` они на одну строку больше, чем в `e3480f58`; файлы, которые перенесены, названы новым путём.

## Итог

- Запросы и команды с одним владельцем: 170 топиков — query 67, command 93, mixed 10.
- События (факт, у публикующего нет обработчика-запроса): 59 топиков.
- Сегодня `request()` зовёт и запросы, и команды: из 93 топиков вида command 83 идут через `request`, 10 — через `send_command`.

### Топики, которые и читают, и меняют (раздел «mixed»)

Для каждого — где проходит граница и разделяется ли он без смены поведения (на 20.11, найденный путь, не окончательное слово).

| Топик | Что читает | Что меняет и когда | Как разделить без смены поведения |
|---|---|---|---|
| `sources.table.get` | таблицу страницы как есть (`_as_it_is`, table.py:219-229) | при `refresh=true`, если таблица не свежая, страница не ждёт пользователя, не заблокирована и не читается, — начинает чтение (`_launch`, table.py:216): запись `reading`, таблицы, события | запрос по `refresh=false` + команда «обновить, если не свежо» с теми же условиями (команда `sources.table.read` читает безусловно, условий свежести не проверяет); вызывающий называет вид параметром сегодня, станет — топиком. Ответ команды — то же `TableGetReply`, так что `composition._table` (module.py:663-677) делает два шага вместо одного |
| `composition.preview` | строки раскладки по таблице и правилам | при `refresh=true` (и без `lines`) через `sources.table.get` начинает чтение страницы | то же условие, что у `sources.table.get`; с `lines` — чистый запрос |
| `planner.series.get`, `planner.references.get` | последний посчитанный вид (`_shown`) | когда вида нет или входы изменились — считает вид, публикует `planner.series.changed`, `series.status.contribution`, пишет флаги (`save_standing`) | команда «посчитать вид» (она уже есть внутри: `_ask_recount`) + запрос, отдающий посчитанное; пока вид не посчитан, запрос должен ждать конца счёта — это ожидание сегодня внутри запроса. Путь без смены поведения не найден: ожидание счёта придётся переложить на вызывающего или оставить внутри запроса |
| `planner.work.get` | идёт ли синхронизация (память и `plan_runs`) | берёт следующий `seq` (`_state`, module.py:1382-1386): порядок состояний | `seq` — часть чтения (по нему скан отличает позднее состояние от раннего); счётчик не мешает считать топик запросом, если считать порядковый номер не состоянием |
| `sources.provider.get` | картину поставщика | берёт следующий номер картины (`_seq`, provider/host.py:157) | как `planner.work.get` |
| `unpack.editor.get` | вид редактора торрента | при первом открытии создаёт недостающие профили пяти вкладок (`rules.profiles.create`) и сохраняет (module.py:183-193) | команда «подготовить редактор» + чистый запрос; вызывающий (шлюз) вызывает команду перед запросом |
| `auth.login` | пароль проверяется чтением | при успехе и устаревших параметрах хэша записывает новый хэш (module.py:78-79) | запись остаётся внутренним шагом модуля после проверки (команда самому себе): наружу топик — запрос с побочной записью модуля в свою таблицу |
| `sources.torrent_file.get` | файл из кэша | при промахе скачивает (`trackerauth.fetch` — команда соседа, куки), пишет файл кэша | если считать кэш и куки не состоянием — запрос; иначе команда «положить в кэш» + запрос по кэшу |
| `sources.video.stream` | разбирает поток (чтение) | ссылка Vibix: пишет файл манифеста и сохраняет ключ | как `sources.torrent_file.get`: запись — кэш артефактов |

Слабая смесь, которую считаем запросом (чтение из сети, журнал, кэш): `sources.parse`, `sources.releases.get`, `sources.titles.search`,
`metadata.details`, `metadata.search`, `torrents.db.progress.list` (кэш имён), `voices.groups.resolve` (дедупликация записи в журнал).

### Топики без вызова в коде

Обработчик есть, вызова по строке топика в `core/`, `modules/`, `web/src`, `run.py`, `tools/` не найдено (возможен вызов через `diag_echo` шлюза,
modules/gateway/module.py:368, — произвольный топик из тела запроса): `torrents.add`, `torrents.composition`, `torrents.db.add`,
`torrents.db.deactivate_all`, `torrents.db.history`, `torrents.delete`, `torrents.drive_incomplete`, `torrents.info.get`, `torrents.pause`,
`torrents.preview.fetch`, `torrents.recheck`, `torrents.register`, `torrents.resume`, `torrents.seasons`, `sources.parse`. Решение владельца:
часть уходит со старым торрентным конвейером (ROADMAP, решения).

## Запросы и команды

### auth

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `auth.exists` | request: gateway | **query** | module.py:82-87 -> repository.py:11-13 SELECT |
| `auth.login` (concurrent) | request: gateway | **mixed** | module.py:69-75 verifies (read); module.py:78-79 -> repository.py:15-21 UPSERT of the hash when its parameters are outdated |
| `auth.password.set` | request: gateway | **command** | module.py:89-96 -> repository.py:15-21 UPSERT |

### catalog

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `catalog.series.create` | request: gateway | **command** | module.py:227-240 -> repository.py:111-132 INSERT series, sources; publishes series.added, series.source.added |
| `catalog.series.delete` | request: gateway | **command** | module.py:267-278 -> repository.py:144-155 DELETE; publishes series.deleted |
| `catalog.series.get` | request: composition, gateway, library, metadata, planner, renaming, scan, torrents | **query** | module.py:216-223 -> repository.py:98-104 SELECT; aggregator.py:94-107 pure |
| `catalog.series.list` | request: composition, gateway, planner, scan, sources, torrents, unpack, voices | **query** | module.py:209-214 -> repository.py:86-93 SELECT |
| `catalog.series.set_save_path` | request: library | **command** | module.py:182-188 -> repository.py:106-109 UPDATE series |
| `catalog.series.touch_scan_time` | send_command: planner, torrents | **command** | module.py:376-389 -> repository.py:157-164 UPDATE; publishes series.updated |
| `catalog.series.update` | request: gateway, scan | **command** | module.py:248-265 -> repository.py:134-142 UPDATE (only if something applied); publishes series.updated |
| `catalog.sources.add` | request: gateway | **command** | module.py:290-298 -> repository.py:190-195 INSERT; publishes series.source.added |
| `catalog.sources.by_profile` | request: composition, rules | **query** | module.py:287-288 -> repository.py:179-183 SELECT |
| `catalog.sources.list` | request: gateway | **query** | module.py:282-285 -> repository.py:173-177 SELECT |
| `catalog.sources.remove` | request: gateway | **command** | module.py:313-324 -> repository.py:205-213 DELETE; publishes series.source.removed |
| `catalog.sources.update` | request: gateway | **command** | module.py:300-311 -> repository.py:197-203 UPDATE; publishes series.source.updated |
| `catalog.viewing.start` | send_command: gateway (api_series.py:170) | **command** | module.py:157-159 -> aggregator.py:68-73 changes the set of viewed series in memory; publishes series.status.changed |
| `catalog.viewing.stop` | send_command: gateway (api_series.py:170) | **command** | module.py:161-163 -> aggregator.py:68-73, as start |

### composition

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `composition.get` | request: gateway | **query** | module.py:239-243 repo.stored SELECT; layout_reply pure |
| `composition.layout` | request: planner | **query** | module.py:245-251 repo.stored SELECT; layout_of pure; the reply is the model itself (no JSON) |
| `composition.preview` (concurrent) | request: gateway | **mixed** | module.py:313-318: with `lines` only rules.apply (read); otherwise sources.table.get with `refresh` (begins a read of the page when true) |
| `composition.queues.get` | request: gateway | **query** | module.py:253-260 repo.stored, voices.settings.get; _queues_reply pure |
| `composition.recompute` (concurrent) | request: scan | **command** | module.py:359-375 -> _settle (730) -> repository.py:71-158 DELETE/INSERT; publishes composition.changed |
| `composition.settings.reset` | request: gateway | **command** | module.py:464-470 -> _edit, as set |
| `composition.settings.set` | request: gateway | **command** | module.py:377-400 -> _edit (486) -> repository.py:149-155; publishes composition.changed |
| `composition.settings.set_many` | request: gateway | **command** | module.py:380-381 -> _set -> _edit, as set |
| `composition.voice_order.set` | request: gateway | **command** | module.py:266-285 repo.write orders; publishes composition.changed |
| `composition.voice_title.set` | request: gateway | **command** | module.py:287-306 repo.write titles; publishes composition.changed |

### conform

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `conform.cancel` | request: gateway, planner | **command** | module.py:384-403 -> client.py:138-144 DELETE /conform/jobs/{id} |
| `conform.job.find` | request: planner | **query** | module.py:420-429 client.py:155-162 GET |
| `conform.job.get` | request: planner | **query** | module.py:442-451 client.py:164-174 GET; the job table in memory is not written |
| `conform.job.keys` | request: planner | **query** | module.py:431-440 client.py:155-162 GET |
| `conform.params.set` | request: gateway | **command** | module.py:259-271 settings.values.set_many (neighbour writes), publishes conform.settings.changed |
| `conform.plot` | request: gateway | **query** | module.py:405-418 client.py:146-153 GET |
| `conform.reverse` | request: planner (через `job()`, module.py:715, 744) | **command** | module.py:324-364 -> client.py:110-116 POST /conform/reverse |
| `conform.settings.get` | request: gateway, planner | **query** | module.py:242-243 settings.values.by_prefix; settings.py:156-160 pure |
| `conform.settings.set` | request: gateway | **command** | module.py:245-257 settings.values.set_many, _use_address (280-290) swaps the client and the feed; publishes conform.settings.changed |
| `conform.submit` | request: planner (через `job()`, module.py:715, 744) | **command** | module.py:292-322 -> client.py:101-136 POST /conform/enqueue (alignment service) |
| `conform.workspace` | request: planner, sources | **query** | module.py:379-382 settings.value.get, paths pure |

### downloads

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `downloads.cancel` | request: gateway | **command** | module.py:258-273 edits the queue, cancels the task, removes the files (remove_download) |
| `downloads.card.remove` | request: gateway | **command** | module.py:316-324 repository.py:46-48 DELETE download_cards |
| `downloads.fetch` | send_command: planner | **command** | module.py:375-397 queues the download, spawns the worker; publishes downloads.fetch.started (sent as a command) |
| `downloads.limits.get` | request: gateway | **query** | module.py:202-203 limits.py:44-45 |
| `downloads.limits.set` | request: gateway | **command** | module.py:205-219 settings.value.set (neighbour writes), places.set |
| `downloads.queue.clear` | request: gateway | **command** | module.py:275-281 empties _fetch_queue, cards.finished, publishes downloads.fetch.finished |
| `downloads.queue.get` | request: gateway | **query** | module.py:283-302 cards.view copies the cards (cards.py:185) |
| `downloads.withdraw` (concurrent) | request: planner | **command** | module.py:238-256 cards.withdrawn, queue filter, runner.cancel |

### library

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `library.directories.list` | request: gateway | **query** | module.py:89-99 storage.call(os.path.isdir / os.listdir) |
| `library.relocation.apply` | request: scan | **command** | module.py:127-205: moves files (os.rename), qBittorrent location, catalog.series.set_save_path, DELETE relocation_tasks; early return {moved: false} reads only |
| `library.relocation.get` | request: gateway | **query** | module.py:123-125 repository.py:33-36 SELECT |
| `library.relocation.plan` | request: gateway | **command** | module.py:103-121 repository.py:15-26 UPDATE/INSERT/DELETE relocation_tasks; publishes library.relocation.planned |

### metadata

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `metadata.details` | request: gateway | **query** | module.py:125-142 HTTP GET to TMDB, no write |
| `metadata.map.get` | request: gateway | **query** | module.py:101-103 repository.py:21-24 SELECT |
| `metadata.map.list` | request: gateway | **query** | module.py:105-106 repository.py:26-27 SELECT |
| `metadata.map.set` | request: gateway | **command** | module.py:108-116 repository.py:29-45 UPSERT; send_command metadata.seasons.recompute |
| `metadata.search` | request: gateway | **query** | module.py:71-97 HTTP GET to TMDB, no write |
| `metadata.seasons.recompute` | send_command: metadata, scan, torrents | **command** | module.py:146-173 repository.py:47-56 UPDATE; publishes series.updated |

### planner

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `planner.delete` | request: gateway | **command** | module.py:1509-1520 repository.py:83-85 INSERT OR IGNORE plan_commands; recount of the view |
| `planner.delete.cancel` | request: gateway | **command** | module.py:1522-1527 repository.py:87-89 DELETE plan_commands |
| `planner.downloaded_counts` | request: gateway | **query** | module.py:1535-1538 repository.py:50-64 SELECT |
| `planner.failures.forget` | request: gateway | **command** | module.py:1558-1575 repository.py:70-77 DELETE plan_failures |
| `planner.files.get` | request: library | **query** | module.py:1545-1556 repository.py:22-25 SELECT, reads the journal file |
| `planner.pack.get` | request: gateway | **query** | module.py:1033-1035 packing.view() copies the cards |
| `planner.pack.remove` | request: gateway | **command** | module.py:1037-1046 packing.forget, repository.py:151-153 DELETE pack_cards; publishes planner.pack.changed |
| `planner.references.get` | request: gateway | **mixed** | module.py:1489-1491 -> _shown_view: the view in memory is read; when absent or stale it is counted (module.py:1420-1487) and published (planner.series.changed, series.status.contribution, save_standing) |
| `planner.seasons` | request: metadata (module.py:177) | **query** | module.py:1540-1543 repository.py:50-64 SELECT |
| `planner.series.get` (concurrent) | request: gateway | **mixed** | module.py:1506-1507 -> _shown_view, as references.get |
| `planner.sync` | request: scan | **command** | module.py:888-901 repository.py:91-97 INSERT plan_runs, spawns the sync; publishes work.series.state |
| `planner.sync.cancel` (concurrent) | request: scan | **command** | module.py:1191-1221 cancels the sync task, downloads.withdraw / work.withdrawn, the jobs of the service |
| `planner.work.get` | request: scan (module.py:661, `{owner}.work.get`) | **mixed** | module.py:1393-1401 reads the run; _state (1382-1386) advances the sequence counter `_seq` that orders the states |

### renaming

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `renaming.process_torrent` (concurrent) | request: torrents | **command** | module.py:67-77: torrents.rename_file (module.py:179,271), torrents.db.files.upsert (module.py:285-286); even dry_run writes pending_rename |

### rules

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `rules.apply` | request: composition, unpack | **query** | module.py:170-182 repository SELECT, compile_profile pure; the compiled profile is cached in memory |
| `rules.format_filename` | request: planner | **query** | module.py:285-295 formatter.py pure |
| `rules.format_torrent_file` | request: renaming, torrents | **query** | module.py:297-318 pure over the profile |
| `rules.profiles.create` | request: gateway, unpack | **command** | module.py:199-201 repository.py:54-59 INSERT |
| `rules.profiles.delete` | request: gateway, unpack | **command** | module.py:210-229 repository.py:76-91 DELETE; publishes rules.profile.changed |
| `rules.profiles.export` | request: scan | **query** | module.py:184-190 repository SELECT |
| `rules.profiles.list` | request: gateway | **query** | module.py:192-195 repository.py:34-36 SELECT |
| `rules.profiles.update` | request: gateway | **command** | module.py:203-208 repository.py:65-74 UPDATE; publishes rules.profile.changed |
| `rules.rules.add` | request: gateway | **command** | module.py:251-257 repository.py:93-107 INSERT; publishes rules.profile.changed |
| `rules.rules.delete` | request: gateway | **command** | module.py:268-274 repository.py:131-144 DELETE; publishes rules.profile.changed |
| `rules.rules.list` | request: gateway | **query** | module.py:231-240 repository.py:38-50 SELECT |
| `rules.rules.reorder` | request: gateway | **command** | module.py:276-281 repository.py:136-139 UPDATE priority; publishes rules.profile.changed |
| `rules.rules.update` | request: gateway | **command** | module.py:259-266 repository.py:109-129 UPDATE; publishes rules.profile.changed |

### scan

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `scan.all.start` | request: gateway | **command** | module.py:341-347 sets _scan_all_running, spawns the pass over all series |
| `scan.edit.begin` | request: gateway | **command** | module.py:238-263 registers the open edit (_edits) that blocks a run |
| `scan.edit.end` | send_command: gateway | **command** | module.py:265-281 closes the edit (_edits, _edits_over) |
| `scan.output.switch` | request: gateway | **command** | module.py:298-323 catalog.series.update (neighbour writes) |
| `scan.run.cancel` (concurrent) | request: gateway | **command** | module.py:435-467 cancels the pass, sources.table.cancel, planner.sync.cancel |
| `scan.runs.get` | request: torrents | **query** | module.py:206-215 repository.py:51-54 SELECT |
| `scan.schedule.set` | request: gateway | **command** | module.py:349-354 settings.value.set (neighbour writes) |
| `scan.series.run` (concurrent) | request: gateway | **command** | module.py:230-234 -> _run_series 401-418 INSERT scan_tasks, planner.sync |
| `scan.status.get` | request: gateway | **query** | module.py:356-358 -> _status_payload 855-868 settings.value.get, reads _running |

### settings

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `settings.paths.add` | request: gateway | **command** | module.py:65-69 -> repository.py:65-67 INSERT OR IGNORE |
| `settings.paths.list` | request: gateway | **query** | module.py:62-63 -> repository.py:61-63 SELECT |
| `settings.paths.remove` | request: gateway | **command** | module.py:71-73 -> repository.py:69-71 DELETE |
| `settings.value.get` | request: conform, downloads, gateway, metadata, scan, sources, torrents | **query** | module.py:37-39 -> repository.py:16-21 SELECT; secrets decrypted |
| `settings.value.set` | request: downloads, gateway, scan, sources, torrents | **command** | module.py:41-45 -> repository.py:23-28 UPSERT; publishes settings.changed |
| `settings.values.by_prefix` | request: conform, gateway, voices | **query** | module.py:56-58 -> repository.py:52-57 SELECT |
| `settings.values.set_many` | request: conform, sources, voices | **command** | module.py:47-54 -> repository.py:30-36 UPSERT; publishes settings.changed per key |

### sources

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `sources.pages.check` | request: scan | **query** | module.py:646-652 provider/gate.py:27-47, string checks |
| `sources.parse` | не найдено | **query** | module.py:457-478 network read of a tracker page; _polite keeps the time of the last request; no table written |
| `sources.players.list` | request: voices | **query** | module.py:597-622 repository.py:162-184 SELECT |
| `sources.provider.get` | request: gateway | **mixed** | module.py:636-637 -> provider/host.py:153-172 view: reads, but takes the next number of the picture (`_seq += 1`) |
| `sources.provider.set` | request: gateway | **command** | module.py:639-640 provider/host.py:120-132 settings.values.set_many, new connector |
| `sources.releases.get` | request: gateway, torrents | **query** | module.py:441-450 -> on_parse (network read) |
| `sources.settings.get` | request: gateway | **query** | module.py:579-590 repository.py:85-92 |
| `sources.site.resolve` | request: gateway, torrents, unpack | **query** | module.py:592-595 table.py:296-301 string work |
| `sources.table.cancel` | request: gateway, scan | **command** | module.py:575-577 table.py:252-263 cancels the read task, mark_failed |
| `sources.table.get` | request: composition, scan | **mixed** | table.py:188-217: refresh=false -> _as_it_is (read); refresh=true and the table not fresh, not waiting for the user, not blocked, not reading -> _launch (337-342): mark_reading, network, save_table, events |
| `sources.table.read` | send_command: gateway | **command** | module.py:572-573 table.py:231-250 _launch: reads the page now |
| `sources.tables.fresh` | request: gateway | **query** | module.py:368-372 repository.py:137-140 SELECT |
| `sources.terms.get` | request: gateway | **query** | module.py:350-366 catalog.series.list, term.minutes |
| `sources.terms.set` | request: gateway | **command** | module.py:374-380 settings.value.set (neighbour writes) |
| `sources.titles.search` | request: gateway | **query** | module.py:624-630 provider/connector.py:173-181 POST /v1/search (a read of the provider) |
| `sources.torrent_file.drop` | send_command: torrents | **command** | module.py:424-430 os.remove through storage.call |
| `sources.torrent_file.get` | request: torrents | **mixed** | module.py:726-744: the cache hit reads; a miss fetches (delivery -> trackerauth.fetch, a command of the neighbour) and writes the cache file |
| `sources.tracker.set_mirrors` | request: gateway | **command** | module.py:416-422 repository.py:102-107 UPDATE trackers; publishes sources.settings.changed |
| `sources.trackers.list` | request: gateway | **query** | module.py:413-414 repository.py:85-92 |
| `sources.video.fetch` (concurrent) | request: downloads | **command** | module.py:688-715 player.transfer writes the target file (alloha_transfer.py:101) |
| `sources.video.fetch.stop` | request: downloads | **command** | module.py:717-722 stops the transfer |
| `sources.video.stream` | request: downloads | **mixed** | module.py:654-686 -> players/streams.py:18-26: the stream is resolved (read); a Vibix link writes the manifest file (vibix.py:281-293) and saves the key (vibix_protocol.py:294-301) |

### torrents

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `torrents.add` | не найдено | **command** | module.py:334-364 qbt.add_magnet / add_torrent_file (POST /api/v2/torrents/add); no caller found |
| `torrents.composition` (concurrent) | не найдено | **query** | module.py:859-905 repository SELECT, qbt GET, rules.format_torrent_file; no caller found |
| `torrents.db.active` | request: library | **query** | module.py:697-704 repository.py:38-41 SELECT, qbt GET torrents/info |
| `torrents.db.add` | не найдено | **command** | module.py:939-944 repository.py:145-160 INSERT; no caller found |
| `torrents.db.deactivate_all` | не найдено | **command** | module.py:706-710 repository.py:83-107 DELETE/UPDATE, then qbt.delete; no caller found |
| `torrents.db.downloaded_counts` | request: gateway | **query** | module.py:946-948 repository.py:162-171 SELECT |
| `torrents.db.files.for_series` | request: gateway | **query** | module.py:855-857 repository.py:242-252 SELECT |
| `torrents.db.files.list` | request: renaming | **query** | module.py:712-716 repository.py:123,254-256 SELECT |
| `torrents.db.files.upsert` | request: renaming | **command** | module.py:718-730 repository.py:322-348 UPSERT, qbt.set_file_priority; publishes series.downloaded.changed |
| `torrents.db.history` | не найдено | **query** | module.py:851-853 repository.py:138-143 SELECT; no caller found |
| `torrents.db.progress.list` | request: gateway | **query** | module.py:1273-1275 repository SELECTs, catalog.series.list; _names_cache is a cache |
| `torrents.delete` | не найдено | **command** | module.py:535-537 qbt.delete; no caller found |
| `torrents.drive_incomplete` | не найдено | **command** | module.py:1057-1087: conditional (a stuck torrent only): repository.py:223-231 upsert_task, _pipe, publishes torrents.queue.changed; no caller found |
| `torrents.files.get` | request: renaming | **query** | module.py:370-372 qbt GET torrents/files |
| `torrents.fs.verify` (concurrent) | send_command: gateway | **command** | module.py:992-1054: repository UPDATE file status, upsert_task, qbt priorities; publishes series.downloaded.changed (sent as a command) |
| `torrents.info.get` | не найдено | **query** | module.py:366-367 qbt GET torrents/info; no caller found |
| `torrents.pause` | не найдено | **command** | module.py:526-527 qbt POST torrents/stop; no caller found |
| `torrents.preview.fetch` (concurrent) | не найдено | **command** | module.py:375-461: a magnet without cache is added to qBittorrent and deleted again (spawned); publishes torrents.preview.ready/failed; no caller found |
| `torrents.queue.get` | request: gateway | **query** | module.py:986-990 reads _pipe |
| `torrents.recheck` | не найдено | **command** | module.py:532-533 qbt POST torrents/recheck; no caller found |
| `torrents.register` | не найдено | **command** | module.py:732-845 repository INSERT/UPDATE, qbt.delete of the replaced; publishes the queue; no caller found |
| `torrents.release.check` | request: gateway, sources | **command** | releases.py:198-226 store.save checked_at (repository.py:405), updates the release in qBittorrent; publishes torrents.release.changed |
| `torrents.release.download` | request: gateway | **command** | releases.py:147-189 qbt.add_torrent_file/add_magnet, store.save; an existing release is returned as it is |
| `torrents.release.drop_files` | send_command: planner | **command** | releases.py:315-337 qbt.delete(delete_files), rmtree, store.save ARCHIVED (sent as a command) |
| `torrents.release.watch` | request: gateway | **command** | releases.py:307-313 store.save watched; publishes torrents.release.changed |
| `torrents.releases.get` | request: gateway, planner, unpack | **query** | module.py:1396-1399 repository.py:394-402 SELECT |
| `torrents.rename_file` | request: renaming | **command** | module.py:540-542 qbt POST torrents/renameFile |
| `torrents.resume` | не найдено | **command** | module.py:529-530 qbt POST torrents/start; no caller found |
| `torrents.seasons` | не найдено | **query** | module.py:950-954 repository.py:184-194 SELECT; no caller found |
| `torrents.set_location` | request: library | **command** | module.py:544-546 qbt POST torrents/setLocation |
| `torrents.staging.get` | request: gateway | **query** | module.py:1437-1438 settings.value.get |
| `torrents.staging.set` | request: gateway | **command** | module.py:1440-1446 settings.value.set, then relocates every release (releases.py:345-357); publishes torrents.staging.changed |

### trackerauth

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `trackerauth.credentials.get` | request: gateway, torrents | **query** | module.py:90-99 -> repository.py:49-57 SELECT, password left out |
| `trackerauth.credentials.set` | request: gateway | **command** | module.py:108-128 -> repository.py:59-73 UPSERT, drop_account_cookies; publishes trackerauth.credentials.changed |
| `trackerauth.fetch` | request: sources | **command** | module.py:209-237: network request, login, cookies saved (repository.py:110-131), session state in memory |
| `trackerauth.secret.get` | request: gateway | **query** | module.py:101-106 -> repository.py:49-57 SELECT |

### unpack

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `unpack.assign` | request: gateway | **command** | module.py:201-206 repository.py:42-54 INSERT/DELETE |
| `unpack.auto` | request: gateway | **command** | module.py:230-233 repository.py:90-96 DELETE |
| `unpack.editor.get` | request: gateway | **mixed** | module.py:174-193: reads the view; _tab_profiles creates the missing profiles of the five tabs through rules.profiles.create and saves them (repository.py:103-108) |
| `unpack.folder.delete` | request: gateway | **command** | module.py:219-223 repository.py:77-84 |
| `unpack.folder.set` | request: gateway | **command** | module.py:213-217 repository.py:73-75,90-96 |
| `unpack.remove` | request: gateway | **command** | module.py:225-228 repository.py:56-60 |
| `unpack.table.get` | request: sources | **query** | module.py:167-172 reads (repository SELECTs, sources.site.resolve, torrents.releases.get, rules.apply); compose in calc, no I/O |
| `unpack.unassign` | request: gateway | **command** | module.py:208-211 repository.py:65-67 |

### voices

| Топик | Сегодня зовут | По сути | Основание |
|---|---|---|---|
| `voices.groups.resolve` | request: composition | **query** | module.py:71-74 reads settings.values.by_prefix; parse and group_of pure; _reported only de-duplicates a log line |
| `voices.players.get` | request: gateway | **query** | module.py:89-93 catalog.series.list, sources.players.list |
| `voices.settings.export` | request: scan | **query** | module.py:95-97 settings.values.by_prefix |
| `voices.settings.get` | request: composition, gateway, planner | **query** | module.py:85-87 _settings, _players, editor.view pure |
| `voices.settings.set` | request: gateway | **command** | module.py:99-112 settings.values.set_many (neighbour writes), publishes voices.settings.changed (also when nothing changed) |

## События

Публикует `publish_event`; подписчики — `self.handle` в других модулях и шлюз по таблице `SSE_MAP` (36 ключей). Все — факты, кроме отмеченного.

| Топик | Издатель | Подписчики (модули) | SSE | Замечание |
|---|---|---|---|---|
| `composition.changed` | composition (module.py:747) | planner | да |  |
| `composition.failed` | composition (module.py:642) | — | да |  |
| `composition.series.reading` | composition (module.py:521); composition (module.py:528) | — | да |  |
| `conform.daemon.up` | conform (module.py:492) | — |  |  |
| `conform.job.finished` | conform (module.py:522) | planner |  |  |
| `conform.job.removed` | conform (module.py:533) | planner |  |  |
| `conform.queue.changed` | conform (module.py:482) | — | да |  |
| `conform.settings.changed` | conform (module.py:256); conform (module.py:270); conform (module.py:494) | — | да |  |
| `downloads.fetch.finished` | downloads (module.py:279, 430) | planner |  |  |
| `downloads.fetch.started` | downloads (module.py:426) | — |  |  |
| `downloads.queue.changed` | downloads (module.py:341) | — | да |  |
| `gateway.sse.clients` | gateway (module.py:469, 496) | catalog, conform, downloads, planner, scan, torrents |  |  |
| `library.relocation.finished` | library (module.py:173); library (module.py:181) | — | да |  |
| `library.relocation.planned` | library (module.py:113); library (module.py:120) | — |  |  |
| `library.relocation.started` | library (module.py:159) | — | да |  |
| `planner.journal.changed` | planner (module.py:1019) | — | да |  |
| `planner.pack.changed` | planner (module.py:1018) | — | да |  |
| `planner.series.changed` | planner (module.py:1417) | — | да |  |
| `planner.sync.trace` | planner (module.py:310) | — |  |  |
| `rules.profile.changed` | rules (module.py:161-166 `_profile_changed`) | composition, unpack |  |  |
| `scan.run.changed` | scan (module.py:616) | composition, downloads, torrents |  |  |
| `scan.status.changed` | scan (module.py:872) | — | да |  |
| `series.added` | catalog (module.py:236) | composition | да |  |
| `series.busy.changed` | catalog (module.py:178) | — | да |  |
| `series.busy.contribution` | library (module.py:216), scan (module.py:619) | catalog |  |  |
| `series.deleted` | catalog (module.py:276) | composition, downloads, library, metadata, planner, scan, sources, torrents, unpack | да |  |
| `series.downloaded.changed` | planner (module.py:1532); torrents (module.py:1186) | — | да |  |
| `series.purged` | composition (505), torrents (983), library (228), sources (295), metadata (122), scan (338), planner (1134) | catalog |  |  |
| `series.source.added` | catalog (module.py:297, 341-346 `series.source.{what}`) | composition, sources, unpack | да |  |
| `series.source.removed` | catalog (module.py:323, 341-346) | composition, sources, torrents, unpack | да |  |
| `series.source.updated` | catalog (module.py:310, 341-346) | composition, sources, unpack | да |  |
| `series.status.changed` | catalog (module.py:203) | — | да |  |
| `series.status.contribution` | torrents, scan (module.py:759), planner (module.py:1380) | catalog |  |  |
| `series.updated` | catalog (module.py:261, 387), metadata (module.py:168) | composition, planner, torrents | да |  |
| `settings.changed` | settings (module.py:44, 53) | downloads, scan, sources, torrents |  |  |
| `sources.pages.forgotten` | sources (module.py:317) | — |  |  |
| `sources.provider.changed` | sources (host.py:59) | — | да |  |
| `sources.provider.manifest` | sources (connector.py:104) | — |  |  |
| `sources.provider.state` | sources (connector.py:81) | — |  |  |
| `sources.settings.changed` | sources (module.py:421) | — | да |  |
| `sources.table.failed` | sources (table.py) | composition, scan | да |  |
| `sources.table.fresh` | sources (module.py) | — | да |  |
| `sources.table.progress` | sources (table.py, through the callback `publish`) | composition | да |  |
| `sources.table.ready` | sources (table.py) | composition, scan | да |  |
| `sources.video.progress` | sources (module.py:699) | downloads |  |  |
| `system.started` | run.py:90 (bus.publish) | gateway, library, planner, scan, sources |  |  |
| `torrents.preview.failed` | torrents (module.py:521) | — | да |  |
| `torrents.preview.ready` | torrents (module.py:516) | — | да |  |
| `torrents.progress.changed` | torrents (module.py:1295) | — | да |  |
| `torrents.queue.changed` | torrents (module.py:1179) | — | да |  |
| `torrents.release.changed` | torrents (releases.py `_told` -> module.py:1329 `_release_event`) | unpack | да |  |
| `torrents.staging.changed` | torrents (module.py:1451) | — | да |  |
| `trackerauth.credentials.changed` | trackerauth (module.py:126) | torrents |  |  |
| `unpack.editor.changed` | unpack (module.py:197) | — | да |  |
| `unpack.read.progress` | unpack (module.py:290) | — | да |  |
| `unpack.table.changed` | unpack (module.py:246 `_changed`) | sources |  |  |
| `voices.settings.changed` | voices (module.py:111) | composition, planner | да |  |
| `work.series.state` | planner (module.py:1388-1391) | scan |  |  |
| `work.withdrawn` | planner (module.py:1231) | downloads |  | planner asks downloads to drop the work of a series (downloads/module.py:232-236 -> _withdraw, the same as the query downloads.withdraw) |

Подписки на несколько топиков одним обработчиком (циклы): sources/module.py:279-280 (`system.started`, `series.source.*`), unpack/module.py:89-90
(`series.source.*`, `series.deleted`), planner/module.py:875-876 (`VIEW_INPUTS`: `series.updated`, `composition.changed`, `voices.settings.changed`).

## Не входит

- `utils.resolveScreenName` — a method of the VK API called through `request` of a site (not a bus topic).
- Ключи настроек (`conform.work_dir`, `voices.groups`, `torrents.staging_dir`, …) и имена сообщений службы выравнивания
  (`conform.snapshot`, `conform.changed`, `conform.progress`) — не топики шины.

