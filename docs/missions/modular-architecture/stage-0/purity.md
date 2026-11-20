# Прототип переноса файлов карты в `calc` — основание О-1


> Строки «не снято» решены: правила `calc` — ROADMAP, «Правила этапа 1»; `core/__init__.py` без переэкспорта с этапа 1 (`aa240216`).
Рабочий документ миссии `modular-architecture`. Ветка `worktree-agent-ad2753f60f688307a`, три коммита поверх `e3480f58`: перенос с правкой
импортов, снятие настоящих зависимостей, документы этапа 0. В `main` не слита.

## Что перенесено (`git mv`, история файлов сохранена)

| Было | Стало |
|---|---|
| `core/plan/{planner,tables,inputs,marks}.py` | `calc/plan/` |
| `core/plan/{executor,files}.py` | `modules/planner/` |
| `core/{variant_queue,subtitle_queue,voice_names}.py` | `calc/` |
| `modules/rules/engine.py` | `calc/rules/engine.py` |
| `modules/composition/{project,settings,levers,queues,compilations}.py` | `calc/layout/` |
| `modules/unpack/layout.py` | `calc/unpack/layout.py` |
| `modules/planner/view.py` | `calc/view/view.py` |
| `modules/planner/packing.py` | разделён: `card_id`, `tracks_of` — `calc/view/packing.py`; книга карточек `PackBook` с часами — осталась `modules/planner/packing.py` |

Правлены импорты в 43 файлах (модули, тесты и помощники тестов, `tools/checks/plan_{real,on_asset,situations}.py`); пакетные `__init__.py`
у `calc` и подпакетов — по строке документации. `modules/rules/__init__.py` перестал переэкспортировать имена движка (их никто не брал оттуда после
правки импортов). Чистые функции внутри `module.py` не тронуты.

## Что не чисто и почему (настоящие зависимости, не пути импорта)

| Где | Что нужно | Как снято в прототипе |
|---|---|---|
| `calc/view/view.py:8` (было) | `view_of` принимает `SeriesSync` — класс исполнителя с `asyncio.Lock`; читает три поля (`containers`, `failed`, `commands`). Прямой импорт даёт круг: `calc.view.view` → `modules.planner.executor` → пакет `modules.planner` → `module.py` → `calc.view.view` (`ImportError` при первом импорте `calc.view.view`) | протокол `SyncState` в `view.py` с этими тремя полями; импорта исполнителя нет |
| `calc/view/packing.py:11,17,45` (было) | `label_of` из `files.py` (диск); `time.monotonic`, `datetime.now` как значения по умолчанию часов; `PackBook` — книга с изменяемым состоянием и часами | `label_of` — в `calc/plan/tables.py` (чистая строка от места серии); `PackBook` и часы остались в модуле, в `calc` — `card_id`, `tracks_of` |
| `calc/layout/settings.py:17`, `calc/plan/marks.py:14`, `calc/rules/engine.py:19`, `calc/unpack/layout.py:27` | `core.reasons.reason` (чистая: `json`) — вне `core.contracts` | не снято: правило «`calc` импортирует только `core.contracts` и `calc`» требует решения — перенести `reasons` в `core.contracts` или разрешить |
| `calc/plan/inputs.py:15` | `core.ignored.parse_memory` (чистая) | то же |
| любой `calc`-файл, кроме `calc/subtitle_queue.py` | `import core.contracts` исполняет `core/__init__.py`, а он импортирует `Bus`, `BaseModule`, `Runner` (`core/__init__.py:1-6`): при импорте любого `calc`-файла загружаются 30–32 модуля `core` (шина, модуль, раннер, логирование, `asyncio`, `uuid`, `shutil`, `tempfile`). Проверено свежим интерпретатором на каждом файле | не снято: чтобы `calc` не тянул шину, `core/__init__.py` не должен ничего импортировать (в проекте 114 строк `from core import …`, часть из них берёт `Bus`, `BaseModule`, `Runner` из пакета) либо контракты выходят из `core` |
| `calc/layout/settings.py` (`retarget`, вызывается из `composition/module.py:889-891`) | обратный вызов с `log.info` — журнал из чистого счёта | не снято: счёт возвращает замечания, журналит модуль |
| `calc/voice_names.py:21` | `lru_cache` на `fold_name` (65 536 записей на процесс) — память процесса, результат от неё не зависит | не снято: решить, остаётся ли память в `calc` |
| `calc/variant_queue.py:47` | `cached_property` (память на экземпляре) | то же |
| `calc/layout/levers.py:141-155` | реестр рычагов — изменяемый словарь, наполняется при импорте; `register`/`unregister` снаружи не зовутся | не тронут: состояние только при импорте |

Нужны ли `asyncio`, шина, база, диск, сеть, часы самим перенесённым файлам: после снятия `view` и `packing` — нет (статический разбор импортов
и вызовов: ни `asyncio`, `os`, `shutil`, `sqlite3`, `socket`, `subprocess`, `time`, `datetime` и им подобных, ни `open`, `print`, `datetime.now`).

## Набор тестов

| Состояние | Результат |
|---|---|
| `e3480f58` (до правок), `-n 6` | 300 из 300, 43,5 с |
| коммит переноса (только импорты) | 299 из 300, 144 с: `tests/test_app_stand.py::test_a_stop_while_the_service_aligns_takes_its_job_up_again_and_sends_none_twice` ждал конца прогона до `STAND_LIMIT_S` = 120 с; по отдельности проходит за 9 с; сбой воспроизведён один раз; к путям импорта отношения не имеет |
| после снятия зависимостей | 300 из 300 в двух прогонах (42,7 и 43,0 с) |

`tools/checks/plan_fuzz.py` не запускается и в `e3480f58`: импортирует `Chaos` из `tests/plan_world.py`, которого там нет (набор сокращён
коммитом `2571ee22`). Три остальных прибора плана (`plan_real`, `plan_on_asset`, `plan_situations`) после правки импортов загружаются.
