# Жизненный цикл ресурсов озвучки: инварианты

Дата: 2026-10-30. Состояние: актуально. Предмет: файлы, папки и задачи сервиса выравнивания, которыми владеет модуль `voices`; проверяется ревью с шестого круга.

## 1. Что во владении

| Что | Где записано | Владелец |
|---|---|---|
| Файл озвучки, выровненная дорожка, дорожка рядом с видео | `voice_dubs`: `temp_path`, `aligned_path`, `output_path`, `move_from` | строка озвучки |
| Задача сервиса | `voice_dubs`: `job_id`, `out_dir`; до ответа — `submit_key` | строки, ждущие её итога |
| То, что строка отпустила | `voice_cleanup`: путь, вид (`path`, `artifact`, `tree`, `job`, `submission`), папка задачи (`scope`), версия | журнал очистки |
| Отправка в пути | память модуля: `_sending` | выполняющий отправку |
| Обязательство повторить | память модуля: исполнитель восстановления (`cleanup:`, `submission:`, `job:`, `series:`); заново из БД при старте | модуль |

## 2. Инварианты

| № | Инвариант | Чем держится | Тесты |
|---|---|---|---|
| И1 | У созданного приложением файла, папки и задачи в любой момент есть владелец: живая строка или запись журнала | передача журналу при сбросе, удалении эпизода и серии, завершении эпизода, выходе озвучки из плана | `test_a_row_lets_go_of_its_files_in_the_same_step_that_the_journal_takes_them`, `test_an_episode_that_left_the_plan_hands_its_tracks_to_the_journal_with_its_rows`, `test_a_failed_voice_over_that_leaves_the_plan_hands_its_file_to_the_journal_with_its_row` |
| И2 | Строка отпускает указатель и запись журнала появляется в одной транзакции | `repository._hand_over`, `execute_many` | тот же первый тест |
| И3 | Запись журнала кончается только в прочитанной версии; неудача её не кончает; передача того же имени поднимает версию | `voice_cleanup.version`, `cleanup_done(путь, версия)`, `note_cleanup_error` | `test_an_entry_is_ended_only_in_the_version_that_was_read`, `test_a_pass_that_removed_the_old_file_does_not_end_the_entry_of_its_new_owner` |
| И4 | Удаление не затрагивает область, пересекающуюся (то же место, вложенное, вышестоящее) с папкой задачи живой строки или записи журнала о задаче либо отправке, не подтверждённой остановленной | `_contested`, `_overlap` | `test_the_folder_above_an_unconfirmed_job_stays_while_the_job_may_write`, `test_a_journaled_name_inside_the_folder_a_running_job_writes_to_is_left_for_later`, `test_an_old_job_that_is_not_confirmed_stopped_cannot_put_its_bytes_into_the_new_result` |
| И5 | Имя находится во владении удаляющего до того, как проверено, не держит ли его живая строка; имя, которое строка держит, не удаляется | `_clear`: `storage.hold(путь)` до `_remove` | `test_a_name_is_owned_before_the_removal_looks_at_who_holds_it`, `test_a_journaled_path_that_a_voice_over_holds_again_is_not_removed` |
| И6 | Каждая постановка пишет в папки своего поколения; итог читается из папки, сохранённой со строкой | `<результат>/<ключ>`, `<кэш>/<ключ>`, `voice_dubs.out_dir` | `test_an_old_job_that_is_not_confirmed_stopped_cannot_put_its_bytes_into_the_new_result` |
| И7 | У принятой сервисом задачи есть владелец: прикреплённые строки или запись журнала до подтверждённой остановки; пока отправка в пути, ни один взгляд на сервис не кончает её запись и не отправляет озвучки заново | `_sending`, `_attach` (задача без озвучек уходит в отмену), `_stop_job`, `_recover_one` | `test_a_submission_accepted_after_its_series_was_deleted_is_cancelled`, `test_a_job_accepted_for_voice_overs_that_are_gone_is_cancelled_at_once`, `test_a_job_found_by_its_key_that_nothing_owns_is_cancelled`, `test_a_submission_on_its_way_is_not_sent_again_when_the_service_comes_back`, `test_a_submission_without_an_answer_whose_voices_are_reset_is_cancelled_by_its_key` |
| И8 | Ошибка вычисления паузы или ожидания не кончает обязательство | `Recovery._delay`, `_limit`, ограничение степени | `tests/test_voices_recovery.py` |
| И9 | Недостоверный ответ сервиса не равен отсутствию: «задачи нет» — только `404` | `DaemonClient.job`, `jobs` | `tests/test_conform_module.py` (разбор ответов) |
| И10 | Запись пути, занятого незавершённым вызовом, отклоняется, а не ставится в очередь; владение длится, пока жив последний пишущий | `core.storage.acquire`, `hold`, `core.proc.spawn` | `tests/test_core_storage.py`, `tests/test_core_proc.py`, `tests/test_ytdlp_direct.py` |

## 3. Операции и инварианты

| Операция | Меняет | Держит |
|---|---|---|
| сброс строки (`reset`), удаление эпизода (`drop_episode`) и серии (`delete_for_series`), завершение эпизода (`settle_episode`), выход озвучки из плана (`drop_unplanned`) | строки, журнал | И1, И2 |
| проход журнала (`_work_off`, `_sweep_cleanup`, исполнитель восстановления → `_clear`, `_stop_job`) | файлы, журнал, сервис | И3, И4, И5, И7 |
| отправка (`_submit` → `_send` → `_attach`) | строки, журнал, `_sending`, сервис | И6, И7 |
| восстановление (`_recover_one`, `_reconcile`, `_resume_obligations`) | строки, журнал, сервис | И7 |
| вызов хранилища и программы (`storage.call`, `hold`, `spawn`) | файлы | И10 |

## 4. Что не гарантируется

| Что | Почему |
|---|---|
| Защита от записи посторонним процессом в имя дорожки | отпечаток файла не хранится |
| Задача, принятая сервисом позже окна ответа моста, когда отправка уже разобрана как неопределённая и затем освобождена | окно — таймаут запроса; позднее принятие не наблюдается |
| Поведение настоящего сервиса: отмена, поиск по метке, запись во вложенные папки | вживую не проверено |
| Обязательство получить итог для обычной принятой задачи | её итог приходит событием; потерю события закрывают возвращение демона и перезапуск |
| Порядок пакетов после восстановления БД из копии | идентификатор истории состояния не реализован |
